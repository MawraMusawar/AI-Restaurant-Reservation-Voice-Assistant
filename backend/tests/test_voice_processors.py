from pipecat.frames.frames import (
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.tests.utils import SleepFrame, run_test

from app.voice.processors import (
    LiveEdgeAudioGate,
    ReplyLatencyProbe,
    SessionRecorder,
    TurnClock,
    is_audio,
    is_text,
)
from app.voice.sessions import VoiceSession

FRAME_SECS = 0.02  # 20 ms frames, as delivered by the WebRTC input transport


def _audio() -> InputAudioRawFrame:
    return InputAudioRawFrame(audio=b"\x00\x00" * 320, sample_rate=16000, num_channels=1)


async def _run_gate(arrivals: list[float]) -> tuple[int, LiveEdgeAudioGate]:
    """Send one 20 ms frame per arrival time through the gate; return frames forwarded.

    The gate reads the clock once per audio frame, so the fake clock simply returns
    the next scheduled arrival time.
    """
    times = iter(arrivals)
    gate = LiveEdgeAudioGate(max_burst_secs=0.2, clock=lambda: next(times))
    down, _ = await run_test(gate, frames_to_send=[_audio() for _ in arrivals])
    return sum(isinstance(f, InputAudioRawFrame) for f in down), gate


async def test_gate_passes_a_steady_real_time_stream() -> None:
    arrivals = [1000.0 + i * FRAME_SECS for i in range(500)]  # 10 s of live audio
    forwarded, gate = await _run_gate(arrivals)
    assert forwarded == 500
    assert gate.total_dropped_secs == 0


async def test_gate_drops_a_startup_backlog() -> None:
    # 5 s of audio queued while the STT was connecting, all delivered at once...
    backlog = [1000.0] * 250
    # ...followed by 2 s of live audio, which must all get through.
    live = [1000.0 + (i + 1) * FRAME_SECS for i in range(100)]
    forwarded, gate = await _run_gate(backlog + live)
    assert forwarded == 10 + 100  # 200 ms burst allowance + all live audio
    assert abs(gate.total_dropped_secs - 4.8) < 1e-6


async def test_gate_tolerates_jitter_but_drops_a_mid_call_stall() -> None:
    steady = [1000.0 + i * FRAME_SECS for i in range(50)]
    # Network jitter: 100 ms of frames arrive late, together. Within the allowance.
    jitter = [steady[-1] + 0.12] * 5
    # Server stalled for 1 s: 50 frames then arrive at once. Only 200 ms survives.
    stall = [jitter[-1] + 1.0] * 50
    forwarded, gate = await _run_gate(steady + jitter + stall)
    assert forwarded == 50 + 5 + 10
    assert abs(gate.total_dropped_secs - 0.8) < 1e-6


def _final(text: str) -> TranscriptionFrame:
    return TranscriptionFrame(text=text, user_id="caller", timestamp="2026-11-06T00:00:00Z")


def _interim(text: str) -> InterimTranscriptionFrame:
    return InterimTranscriptionFrame(text=text, user_id="caller", timestamp="2026-11-06T00:00:00Z")


async def test_session_recorder_marks_session_ready_on_start() -> None:
    session = VoiceSession(session_id="s1")
    await run_test(SessionRecorder(session), frames_to_send=[], expected_down_frames=[])
    assert session.ready_at is not None
    assert session.startup_ms is not None and session.startup_ms >= 0


async def test_session_recorder_passes_transcripts_through_unchanged() -> None:
    # The caller's words are recorded per turn by the user aggregator, not here.
    session = VoiceSession(session_id="s1")
    await run_test(
        SessionRecorder(session),
        frames_to_send=[_interim("a table"), _final("A table for four."), _final("   ")],
        expected_down_frames=[InterimTranscriptionFrame, TranscriptionFrame, TranscriptionFrame],
    )
    assert session.transcript == []
    assert session.stt_latencies_ms == []


async def test_session_recorder_measures_latency_after_end_of_speech() -> None:
    session = VoiceSession(session_id="s1")
    await run_test(
        SessionRecorder(session),
        # VAD frames are system frames that jump the queue; SleepFrame keeps the
        # real-time ordering of a live call.
        frames_to_send=[
            VADUserStartedSpeakingFrame(),
            SleepFrame(sleep=0.05),
            _final("Early final from Deepgram endpointing."),  # before VAD stop: no latency
            SleepFrame(sleep=0.05),
            VADUserStoppedSpeakingFrame(),
            SleepFrame(sleep=0.05),
            _final("Final after finalize."),  # after VAD stop: latency recorded
        ],
        expected_down_frames=[
            VADUserStartedSpeakingFrame,
            TranscriptionFrame,
            VADUserStoppedSpeakingFrame,
            TranscriptionFrame,
        ],
    )
    assert len(session.stt_latencies_ms) == 1
    assert 40 <= session.stt_latencies_ms[0] < 1000  # ~the 50 ms gap we inserted


async def test_reply_probes_measure_first_text_and_first_audio_once_per_turn() -> None:
    times = iter([10.0, 10.6, 11.1, 20.0, 20.4])
    turn_clock = TurnClock(clock=lambda: next(times))
    text_ms: list[float] = []
    audio_ms: list[float] = []
    pipeline = Pipeline(
        [
            ReplyLatencyProbe(turn_clock, text_ms, "text", is_text),
            ReplyLatencyProbe(turn_clock, audio_ms, "audio", is_audio),
        ]
    )
    turn_clock.mark_user_turn_end()  # t=10.0
    await run_test(
        pipeline,
        frames_to_send=[
            LLMTextFrame(""),  # empty tokens don't count
            LLMTextFrame("Sure,"),  # t=10.6 -> 600 ms to first text
            _tts_audio(),  # t=11.1 -> 1100 ms to first audio
            LLMTextFrame(" what time?"),
            _tts_audio(),  # later frames of the same turn are ignored
        ],
        expected_down_frames=[
            LLMTextFrame,
            LLMTextFrame,
            TTSAudioRawFrame,
            LLMTextFrame,
            TTSAudioRawFrame,
        ],
    )
    assert text_ms == [600.0] and audio_ms == [1100.0]


async def test_reply_probe_ignores_output_without_a_caller_turn() -> None:
    # e.g. the greeting, spoken before the caller has said anything.
    audio_ms: list[float] = []
    await run_test(
        ReplyLatencyProbe(TurnClock(), audio_ms, "audio", is_audio),
        frames_to_send=[_tts_audio()],
        expected_down_frames=[TTSAudioRawFrame],
    )
    assert audio_ms == []


def _tts_audio() -> TTSAudioRawFrame:
    return TTSAudioRawFrame(audio=b"\x00\x00" * 480, sample_rate=24000, num_channels=1)


def test_turn_clock_measures_turn_detection_from_vad_stop() -> None:
    times = iter([5.0, 5.8, 9.0])
    clock = TurnClock(clock=lambda: next(times))
    clock.mark_speech_stopped()  # t=5.0: the VAD heard silence
    assert clock.mark_user_turn_end() == 800.0  # t=5.8: turn ended 0.8 s later
    assert clock.mark_user_turn_end() is None  # t=9.0: no speech stop since (e.g. typed)
    assert clock.turn == 2
