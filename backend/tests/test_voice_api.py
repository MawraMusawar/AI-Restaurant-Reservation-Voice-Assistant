"""Signaling API tests, plus real end-to-end transcription over WebRTC.

The end-to-end tests use aiortc (installed with pipecat-ai[webrtc]) in place of
the browser. They stream speech WAV fixtures to the agent and check what
Deepgram transcribed and how the caller's turns were detected. They need
DEEPGRAM_API_KEY and use a few seconds of the Deepgram free quota per test.
The LLM is replaced by a stub that never answers, so these tests spend no Groq
quota (the LLM itself is tested in test_agent_llm.py).
"""

import asyncio
import fractions
import time
import wave
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import av
import httpx
import numpy as np
import pytest
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from pipecat.frames.frames import Frame, LLMContextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pydantic import SecretStr

from app.config import Settings, get_settings
from app.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"
AUDIO_SDP = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"


CONFIGURED = {
    "deepgram_api_key": "test-key",
    "groq_api_key": "test-key",
    "elevenlabs_api_key": "test-key",
}


class SilentLLM(FrameProcessor):
    """Stands in for the LLM: swallows inference requests, passes everything else."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if not isinstance(frame, LLMContextFrame):
            await self.push_frame(frame, direction)


@pytest.fixture
def silent_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.voice.bot.create_llm_service", lambda *_a, **_k: SilentLLM())


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            c.app = app  # type: ignore[attr-defined]
            yield c


# --- Validation and readiness ------------------------------------------------------


async def test_offer_rejects_missing_sdp(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/offer", json={"type": "offer"})
    assert response.status_code == 422


async def test_offer_rejects_non_offer_type(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/offer", json={"sdp": AUDIO_SDP, "type": "answer"})
    assert response.status_code == 422


async def test_offer_with_malformed_sdp_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/offer", json={"sdp": "not an sdp", "type": "offer"})
    assert response.status_code == 422
    # No session must be created for a junk offer.
    assert (await client.get("/api/voice/sessions")).json() == []


async def test_offer_without_audio_stream_is_rejected(client: httpx.AsyncClient) -> None:
    sdp = "v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\ns=-\r\nt=0 0\r\n"
    response = await client.post("/api/offer", json={"sdp": sdp, "type": "offer"})
    assert response.status_code == 422


async def test_offer_returns_503_when_all_lines_busy(client: httpx.AsyncClient) -> None:
    client.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[attr-defined]
        _env_file=None, **CONFIGURED
    )
    client.app.state.voice.sessions._max_concurrent = 0  # type: ignore[attr-defined]
    response = await client.post("/api/offer", json={"sdp": AUDIO_SDP, "type": "offer"})
    assert response.status_code == 503
    assert "busy" in response.json()["detail"]


async def test_offer_returns_503_when_deepgram_not_configured(client: httpx.AsyncClient) -> None:
    client.app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)  # type: ignore[attr-defined]
    response = await client.post("/api/offer", json={"sdp": AUDIO_SDP, "type": "offer"})
    assert response.status_code == 503
    assert "Deepgram" in response.json()["detail"]
    assert (await client.get("/api/voice/sessions")).json() == []


async def test_voice_status_reports_missing_credentials(client: httpx.AsyncClient) -> None:
    client.app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)  # type: ignore[attr-defined]
    body = (await client.get("/api/voice/status")).json()
    assert body["ready"] is False
    assert body["missing_credentials"] == ["Deepgram", "Groq", "ElevenLabs"]
    assert "Deepgram" in body["detail"] and "Groq" in body["detail"]


async def test_voice_status_ready_when_configured(client: httpx.AsyncClient) -> None:
    client.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[attr-defined]
        _env_file=None, **CONFIGURED
    )
    body = (await client.get("/api/voice/status")).json()
    assert body == {
        "ready": True,
        "missing_credentials": [],
        "active_sessions": 0,
        "max_sessions": 2,
        "detail": None,
        "bookings_online": client.app.state.db is not None,  # type: ignore[attr-defined]
    }


async def test_ice_patch_for_unknown_connection_returns_404(client: httpx.AsyncClient) -> None:
    response = await client.patch(
        "/api/offer",
        json={"pc_id": "missing", "candidates": [{"candidate": "", "sdp_mid": "0"}]},
    )
    assert response.status_code == 404


async def test_sessions_endpoint_starts_empty(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/voice/sessions")
    assert response.status_code == 200
    assert response.json() == []


# --- End-to-end: speech over WebRTC -> Deepgram -> caller turns ---------------------------


class WavTrack(MediaStreamTrack):
    """A microphone that stays silent until `speak` is set, then plays a WAV in real time.

    Like a real caller, it only talks once the call is up. Audio sent before the
    pipeline is ready is dropped as stale by `LiveEdgeAudioGate`. With
    `noise_snr_db` set, white noise is mixed in at that signal-to-noise ratio
    to simulate a noisy room. Trailing silence lets the VAD detect end of speech.
    `speech_ended_at` records the wall-clock moment the last speech sample was sent.
    """

    kind = "audio"
    FRAME_MS = 20

    def __init__(self, path: Path | None, noise_snr_db: float | None = None) -> None:
        super().__init__()
        self.sample_rate = 16000
        samples = np.zeros(self.sample_rate * 2, dtype=np.float64)
        if path is not None:
            with wave.open(str(path), "rb") as w:
                assert w.getframerate() == 16000 and w.getnchannels() == 1
                samples = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(
                    np.float64
                )
        if noise_snr_db is not None:
            rng = np.random.default_rng(seed=42)
            signal_power = np.mean(samples**2)
            noise_power = signal_power / (10 ** (noise_snr_db / 10))
            samples = samples + rng.normal(0, np.sqrt(noise_power), samples.shape)
        self._audio = np.clip(samples, -32768, 32767).astype(np.int16)
        self._chunk = self.sample_rate * self.FRAME_MS // 1000
        self._sent = 0  # samples sent in total (sets the real-time pace)
        self._speech_pos = 0  # position in the WAV once speaking
        self._start: float | None = None
        self.speak = asyncio.Event()
        self.speech_ended_at: datetime | None = None

    @property
    def duration_secs(self) -> float:
        return len(self._audio) / self.sample_rate

    @property
    def speech_onset_secs(self) -> float:
        """Leading silence before the first word (the TTS fixtures start with ~0.1 s)."""
        loud = np.flatnonzero(np.abs(self._audio.astype(np.int32)) > 1000)
        return float(loud[0]) / self.sample_rate if loud.size else 0.0

    async def recv(self) -> av.AudioFrame:
        if self._start is None:
            self._start = time.monotonic()
        delay = self._start + self._sent / self.sample_rate - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        chunk = np.zeros(self._chunk, dtype=np.int16)
        if self.speak.is_set() and self._speech_pos < len(self._audio):
            part = self._audio[self._speech_pos : self._speech_pos + self._chunk]
            chunk[: len(part)] = part
            self._speech_pos += self._chunk
            if self._speech_pos >= len(self._audio):
                self.speech_ended_at = datetime.now(UTC)
        frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = self.sample_rate
        frame.pts = self._sent
        frame.time_base = fractions.Fraction(1, self.sample_rate)
        self._sent += self._chunk
        return frame


async def _wait_for_session(
    client: httpx.AsyncClient, predicate: Callable[[dict], bool], within_secs: float
) -> dict:
    deadline = time.monotonic() + within_secs
    sessions: list = []
    while time.monotonic() < deadline:
        sessions = (await client.get("/api/voice/sessions")).json()
        if sessions and predicate(sessions[0]):
            return sessions[0]
        await asyncio.sleep(0.1)
    raise AssertionError(f"timed out waiting for session state: {sessions}")


async def _call_and_get_transcript(
    client: httpx.AsyncClient, track: WavTrack, wait_after_secs: float = 6.0
) -> dict:
    """Place a WebRTC call that plays `track`, hang up, and return the finished session."""
    pc = RTCPeerConnection()
    pc.addTrack(track)
    try:
        await pc.setLocalDescription(await pc.createOffer())
        response = await client.post(
            "/api/offer", json={"sdp": pc.localDescription.sdp, "type": "offer"}
        )
        assert response.status_code == 200, response.text
        answer = response.json()
        await pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type="answer"))
        # Speak only once the pipeline is live (the browser shows "On call" at this point).
        await _wait_for_session(client, lambda s: s["ready_at"] is not None, within_secs=20)
        track.speak.set()
        # Let the whole clip play, then give Deepgram time to deliver the final result.
        await asyncio.sleep(track.duration_secs + wait_after_secs)
    finally:
        await pc.close()

    return await _wait_for_session(
        client, lambda s: s["status"] in ("ended", "error"), within_secs=10
    )


def _end_of_speech_to_turn_end_ms(session: dict, track: WavTrack) -> float:
    """Wall-clock time from the caller's last word to the end of their turn.

    That is when the LLM receives the caller's words: the VAD stop delay,
    Deepgram finalize, and smart-turn end-of-turn detection are all included.
    """
    user = [t for t in session["transcript"] if t["role"] == "user"]
    assert track.speech_ended_at is not None and user
    turn_end = datetime.fromisoformat(user[-1]["at"])
    return (turn_end - track.speech_ended_at).total_seconds() * 1000


def _text(session: dict) -> str:
    return " ".join(t["text"] for t in session["transcript"] if t["role"] == "user").lower()


needs_deepgram = pytest.mark.skipif(
    get_settings().deepgram_api_key is None, reason="DEEPGRAM_API_KEY not configured"
)


@pytest.mark.integration
@needs_deepgram
@pytest.mark.parametrize(
    ("fixture", "snr_db", "expected_any"),
    [
        ("booking_normal.wav", None, [["table"], ["four", "4"], ["seven", "7"]]),
        ("correction_fast.wav", None, [["six thirty", "6:30", "630"], ["sarah"]]),
        ("question_slow.wav", None, [["close"], ["sunday"]]),
        ("booking_normal.wav", 5.0, [["table"], ["four", "4"]]),  # noisy room, SNR 5 dB
    ],
    ids=["normal-speech", "fast-speech", "slow-speech", "background-noise-5dB"],
)
async def test_live_transcription_over_webrtc(
    client: httpx.AsyncClient,
    silent_llm: None,
    fixture: str,
    snr_db: float | None,
    expected_any: list[list[str]],
) -> None:
    track = WavTrack(FIXTURES / fixture, snr_db)
    session = await _call_and_get_transcript(client, track)
    text = _text(session)
    e2e_ms = _end_of_speech_to_turn_end_ms(session, track)
    print(
        f"\n[{fixture} snr={snr_db}] startup_ms={session['startup_ms']} "
        f"end_of_speech_to_turn_end_ms={e2e_ms:.0f} finalize_ms={session['stt_latencies_ms']}"
        f"\n  transcript={text!r} dropped_audio_secs={session['dropped_audio_secs']} "
        f"max_loop_lag_ms={session['max_loop_lag_ms']} loop_stalls={session['loop_stalls']}"
    )

    assert session["status"] == "ended", session
    assert session["error"] is None, session
    for alternatives in expected_any:
        assert any(word in text for word in alternatives), (alternatives, text)
    # STT budget: once the VAD hears silence, Deepgram's finalized transcript must
    # follow quickly (measured 110-610 ms on the EU endpoint, Milestone 2).
    assert all(ms < 1000 for ms in session["stt_latencies_ms"]), session["stt_latencies_ms"]
    # Turn budget: the caller's turn reaches the LLM as soon as smart-turn judges the
    # sentence complete (~0.5-1 s after the last word), or after its 3 s silence fallback
    # when it judges it unfinished. The synthetic voices' flat intonation sometimes
    # triggers the fallback; tuning this trade-off is Milestone 5 (turn-taking).
    assert e2e_ms < 3500, f"turn ended {e2e_ms:.0f} ms after end of speech"


@pytest.mark.integration
async def test_rejected_deepgram_key_ends_call_with_error(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, silent_llm: None
) -> None:
    """A real Deepgram rejection (invalid key) must end the call and record why."""
    bad = get_settings().model_copy(update={"deepgram_api_key": SecretStr("0" * 40)})
    monkeypatch.setattr("app.voice.bot.get_settings", lambda: bad)
    client.app.dependency_overrides[get_settings] = lambda: bad  # type: ignore[attr-defined]

    pc = RTCPeerConnection()
    pc.addTrack(WavTrack(None))
    try:
        await pc.setLocalDescription(await pc.createOffer())
        response = await client.post(
            "/api/offer", json={"sdp": pc.localDescription.sdp, "type": "offer"}
        )
        assert response.status_code == 200, response.text
        answer = response.json()
        await pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type="answer"))
        # The pipeline must end by itself (ProcessorUnusablePolicy.END), not wait for hang-up.
        session = await _wait_for_session(
            client, lambda s: s["status"] in ("ended", "error"), within_secs=20
        )
    finally:
        await pc.close()

    assert session["status"] == "error", session
    assert "DeepgramSTTService" in session["error"], session
    assert "401" in session["error"] or "reject" in session["error"].lower(), session
    assert session["transcript"] == []


@pytest.mark.integration
@needs_deepgram
async def test_silence_produces_no_transcript(client: httpx.AsyncClient, silent_llm: None) -> None:
    session = await _call_and_get_transcript(client, WavTrack(None), wait_after_secs=3.0)
    assert session["status"] == "ended", session
    assert session["transcript"] == []
    assert session["startup_ms"] is not None


@pytest.mark.integration
@needs_deepgram
async def test_rejected_groq_key_ends_call_with_error(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real Groq rejection (invalid key) on the caller's first turn ends the call."""
    real = get_settings()
    bad = real.model_copy(update={"groq_api_key": SecretStr("gsk_" + "0" * 52)})
    monkeypatch.setattr("app.voice.bot.get_settings", lambda: bad)

    session = await _call_and_get_transcript(client, WavTrack(FIXTURES / "booking_normal.wav"))
    assert session["status"] == "error", session
    assert "GroqLLMService" in session["error"], session
    assert "401" in session["error"] or "invalid" in session["error"].lower(), session
    roles = {t["role"] for t in session["transcript"]}
    assert roles == {"user"}, session  # the caller was heard, but never answered


# --- End-to-end with speech output (ElevenLabs): greeting, reply, barge-in -----------------

SPEAKING_RMS = 300  # int16 RMS above this = the assistant is audibly talking


class BotAudioMonitor:
    """Plays the caller's speaker: records the loudness of the assistant's audio over time."""

    def __init__(self) -> None:
        self.samples: list[tuple[float, float]] = []  # (monotonic time, RMS)
        self._task: asyncio.Task[None] | None = None

    def attach(self, track: MediaStreamTrack) -> None:
        self._task = asyncio.create_task(self._consume(track))

    async def _consume(self, track: MediaStreamTrack) -> None:
        while True:
            try:
                frame = await track.recv()
            except Exception:
                return
            pcm = frame.to_ndarray().astype(np.float64)
            self.samples.append((time.monotonic(), float(np.sqrt(np.mean(pcm**2)))))

    def first_speech_after(self, t: float) -> float | None:
        return next((at for at, rms in self.samples if at >= t and rms > SPEAKING_RMS), None)

    def silent_from(self, t: float, hold_secs: float = 0.3) -> float | None:
        """First moment after `t` when the audio stays below the speaking level for `hold_secs`."""
        quiet_since: float | None = None
        for at, rms in self.samples:
            if at < t:
                continue
            if rms > SPEAKING_RMS:
                quiet_since = None
            elif quiet_since is None:
                quiet_since = at
            elif at - quiet_since >= hold_secs:
                return quiet_since
        return None

    def stop(self) -> None:
        if self._task:
            self._task.cancel()


CLIENT_READY = (
    '{"label": "rtvi-ai", "type": "client-ready", "id": "test-1", '
    '"data": {"version": "2.3.0", "about": {"library": "aiortc-test"}}}'
)


async def _start_call(client: httpx.AsyncClient, track: WavTrack):
    """Connect like the browser does: audio both ways, plus the RTVI client-ready message."""
    pc = RTCPeerConnection()
    pc.addTrack(track)
    channel = pc.createDataChannel("rtvi")
    monitor = BotAudioMonitor()
    pc.on("track", lambda remote: monitor.attach(remote) if remote.kind == "audio" else None)
    await pc.setLocalDescription(await pc.createOffer())
    response = await client.post(
        "/api/offer", json={"sdp": pc.localDescription.sdp, "type": "offer"}
    )
    assert response.status_code == 200, response.text
    answer = response.json()
    await pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type="answer"))
    await _wait_for_session(client, lambda s: s["ready_at"] is not None, within_secs=20)
    for _ in range(50):
        if channel.readyState == "open":
            break
        await asyncio.sleep(0.1)
    channel.send(CLIENT_READY)  # -> bot-ready, then the spoken greeting
    return pc, monitor


async def _hang_up(client: httpx.AsyncClient, pc: RTCPeerConnection, monitor: BotAudioMonitor):
    monitor.stop()
    await pc.close()
    return await _wait_for_session(
        client, lambda s: s["status"] in ("ended", "error"), within_secs=10
    )


needs_voice = pytest.mark.skipif(
    get_settings().elevenlabs_api_key is None or get_settings().deepgram_api_key is None,
    reason="DEEPGRAM_API_KEY and ELEVENLABS_API_KEY required",
)


@pytest.mark.integration
@needs_voice
async def test_caller_interrupts_the_greeting(client: httpx.AsyncClient, silent_llm: None) -> None:
    """Barge-in: speaking over the assistant stops its audio and keeps only what was said."""
    track = WavTrack(FIXTURES / "booking_normal.wav")
    pc, monitor = await _start_call(client, track)
    try:
        started = time.monotonic()
        bot_started = None
        while bot_started is None and time.monotonic() - started < 10:
            await asyncio.sleep(0.05)
            bot_started = monitor.first_speech_after(started)
        assert bot_started is not None, "the greeting was never heard"
        greeting_secs = bot_started - started
        # Talk over the greeting 1.2 s after it starts.
        await asyncio.sleep(max(0.0, bot_started + 1.2 - time.monotonic()))
        track.speak.set()
        caller_started = time.monotonic() + track.speech_onset_secs  # first word
        first_word_wall = datetime.now() + timedelta(seconds=track.speech_onset_secs)
        await asyncio.sleep(4)
        # 0.6 s of quiet: longer than the pauses between the greeting's sentences.
        bot_silent = monitor.silent_from(caller_started, hold_secs=0.6)
    finally:
        session = await _hang_up(client, pc, monitor)

    print("\n[barge-in] loudness per 100 ms from greeting start (caller at *):")
    print(" ".join(_timeline(monitor, bot_started, caller_started)))
    assert bot_silent is not None, "the assistant kept talking over the caller"
    stop_ms = (bot_silent - caller_started) * 1000
    print(
        f"\n[barge-in] greeting heard after {greeting_secs:.2f}s; "
        f"audio stopped {stop_ms:.0f} ms after the caller's first word"
    )
    print(f"[barge-in] caller's first word at {first_word_wall:%H:%M:%S.%f} (wall clock)")
    turns = session["transcript"]
    print(
        f"[barge-in] dropped_audio_secs={session['dropped_audio_secs']} "
        f"max_loop_lag_ms={session['max_loop_lag_ms']} stt={session['stt_latencies_ms']} "
        f"transcript={[(t['role'], t['text'][:30], t['interrupted']) for t in turns]}"
    )
    # VAD needs VOICE_VAD_START_SECS (0.4 s) of continuous voice; this fixture opens with
    # "Hi," and a pause. Measured with server and client in separate processes (as with a
    # browser): 1.2-1.6 s. In this test the client shares the server's process and CPU, so
    # the bound is looser; it checks that barge-in works, not the exact timing.
    assert stop_ms < 2500, f"assistant audio stopped {stop_ms:.0f} ms after the caller's first word"
    greeting = next(t for t in session["transcript"] if t["role"] == "assistant")
    assert greeting["interrupted"] is True, session["transcript"]
    # Only what was actually spoken is kept (and goes into the LLM context).
    full = "Thank you for calling Olive & Ember, this is Sara. How can I help you today?"
    assert greeting["text"] != full and full.startswith(greeting["text"].rstrip(".,")), greeting


@pytest.mark.integration
@needs_voice
async def test_assistant_answers_out_loud(client: httpx.AsyncClient) -> None:
    """Real Deepgram + Groq + ElevenLabs: the caller asks, and hears the answer."""
    track = WavTrack(FIXTURES / "question_slow.wav")  # "...what time do you close on Sunday?"
    pc, monitor = await _start_call(client, track)
    try:
        # Let the greeting finish, as a polite caller would.
        started = time.monotonic()
        await asyncio.sleep(2)
        greeting_end = monitor.silent_from(started + 1.0, hold_secs=0.8)
        for _ in range(60):
            if greeting_end is not None:
                break
            await asyncio.sleep(0.25)
            greeting_end = monitor.silent_from(started + 1.0, hold_secs=0.8)
        assert greeting_end is not None, "the greeting never finished"
        track.speak.set()
        await asyncio.sleep(track.duration_secs + 8)
        speech_end = time.monotonic() - (datetime.now(UTC) - track.speech_ended_at).total_seconds()
        reply_heard = monitor.first_speech_after(speech_end)
    finally:
        session = await _hang_up(client, pc, monitor)

    roles = [t["role"] for t in session["transcript"]]
    print(
        f"\n[spoken reply] voice_latency_ms={session['voice_latencies_ms']} "
        f"text_latency_ms={session['response_latencies_ms']} transcript={session['transcript']}"
    )
    assert session["status"] == "ended" and session["error"] is None, session
    assert reply_heard is not None, "no spoken reply was heard"
    heard_ms = (reply_heard - speech_end) * 1000
    print(f"[spoken reply] caller's last word -> reply heard: {heard_ms:.0f} ms")
    assert roles[0] == "assistant" and "user" in roles and roles[-1] == "assistant", roles
    reply = session["transcript"][-1]["text"]
    assert "9:30" in reply or "21:30" in reply or "nine thirty" in reply.lower(), reply
    assert session["voice_latencies_ms"], session
    # Turn end (smart-turn, worst case its 3 s fallback) + LLM + one tool call + TTS first audio.
    assert heard_ms < 5000, f"reply heard {heard_ms:.0f} ms after the caller's last word"


def _timeline(monitor: BotAudioMonitor, start: float, mark: float, secs: float = 9.0) -> list[str]:
    buckets: list[str] = []
    for i in range(int(secs * 10)):
        lo, hi = start + i / 10, start + (i + 1) / 10
        values = [rms for at, rms in monitor.samples if lo <= at < hi]
        peak = max(values, default=0)
        buckets.append(("*" if lo <= mark < hi else "") + str(int(peak // 100)))
    return buckets


@pytest.mark.integration
@needs_voice
async def test_silent_caller_is_checked_on_then_the_call_ends(
    client: httpx.AsyncClient, silent_llm: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Silence: "Are you still there?", then a goodbye, then the call ends by itself."""
    fast = get_settings().model_copy(update={"voice_silence_prompt_secs": 2.0})  # 8 s default
    monkeypatch.setattr("app.voice.bot.get_settings", lambda: fast)
    pc, monitor = await _start_call(client, WavTrack(None))
    try:
        session = await _wait_for_session(
            client, lambda s: s["status"] in ("ended", "error"), within_secs=40
        )
    finally:
        monitor.stop()
        await pc.close()

    assert session["status"] == "ended" and session["end_reason"] == "caller_silent", session
    # Greeting, check-in and goodbye were all heard: three separate bursts of speech.
    bursts, last = 0, -1.0
    for at, rms in monitor.samples:
        if rms > SPEAKING_RMS:
            if at - last > 1.0:
                bursts += 1
            last = at
    print(f"\n[silence] speech bursts heard: {bursts}, duration {session['duration_seconds']}s")
    assert bursts >= 3, f"expected greeting + check-in + goodbye, heard {bursts} burst(s)"
    said = [t["text"] for t in session["transcript"] if t["role"] == "assistant"]
    assert any("still there" in t for t in said) and any("Goodbye" in t for t in said), said


@pytest.mark.integration
@needs_voice
async def test_one_word_backchannel_does_not_interrupt(
    client: httpx.AsyncClient, silent_llm: None
) -> None:
    """ "Okay." while the assistant speaks must not cut it off (needs 2+ words)."""
    track = WavTrack(FIXTURES / "backchannel_okay.wav")
    pc, monitor = await _start_call(client, track)
    try:
        started = time.monotonic()
        bot_started = None
        while bot_started is None and time.monotonic() - started < 10:
            await asyncio.sleep(0.05)
            bot_started = monitor.first_speech_after(started)
        assert bot_started is not None, "the greeting was never heard"
        await asyncio.sleep(max(0.0, bot_started + 1.0 - time.monotonic()))
        track.speak.set()
        await asyncio.sleep(6)
    finally:
        session = await _hang_up(client, pc, monitor)

    greeting = next(t for t in session["transcript"] if t["role"] == "assistant")
    assert greeting["interrupted"] is False, session["transcript"]
    assert greeting["text"].endswith("How can I help you today?"), greeting
