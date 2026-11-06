from pipecat.frames.frames import (
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    LLMTextFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.tests.utils import SleepFrame, run_test

from app.voice.processors import LiveEdgeAudioGate, ResponseLatencyRecorder, SessionRecorder
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


async def test_response_latency_runs_from_turn_end_to_first_non_empty_text() -> None:
    session = VoiceSession(session_id="s1")
    times = iter([10.0, 10.75])  # turn end, then the first real token
    recorder = ResponseLatencyRecorder(session, clock=lambda: next(times))
    recorder.mark_user_turn_end()
    await run_test(
        recorder,
        frames_to_send=[LLMTextFrame(""), LLMTextFrame("Sure,"), LLMTextFrame(" what time?")],
        expected_down_frames=[LLMTextFrame, LLMTextFrame, LLMTextFrame],
    )
    assert session.response_latencies_ms == [750.0]


async def test_response_latency_ignores_text_without_a_caller_turn() -> None:
    # e.g. a reply triggered by the system, not by the caller finishing a turn.
    session = VoiceSession(session_id="s1")
    await run_test(
        ResponseLatencyRecorder(session),
        frames_to_send=[LLMTextFrame("Hello")],
        expected_down_frames=[LLMTextFrame],
    )
    assert session.response_latencies_ms == []
