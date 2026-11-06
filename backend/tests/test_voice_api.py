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
from datetime import UTC, datetime
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


CONFIGURED = {"deepgram_api_key": "test-key", "groq_api_key": "test-key"}


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
    assert body["missing_credentials"] == ["Deepgram", "Groq"]
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
        f"\n  transcript={text!r}"
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
    bad = Settings(_env_file=None, deepgram_api_key="0" * 40, groq_api_key="unused")
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
