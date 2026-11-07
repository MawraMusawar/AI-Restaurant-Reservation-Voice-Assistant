"""Custom Pipecat frame processors used by the voice pipeline."""

import time
from collections.abc import Callable

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    LLMTextFrame,
    StartFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.voice.sessions import VoiceSession


class LiveEdgeAudioGate(FrameProcessor):
    """Keep the pipeline at the live edge of the caller's audio by dropping backlogs.

    Audio that arrives faster than real time is a backlog, for example audio
    queued while the STT WebSocket was still connecting (measured at 1.5-7 s
    against Deepgram US), or audio queued while the server was stalled. Deepgram
    returns results at roughly real-time pace, so a backlog becomes a delay that
    lasts for the whole call. Stale audio has no value in a live conversation.

    A token bucket refilled in real time and capped at `max_burst_secs` lets
    steady 20 ms frames and normal network jitter through, and passes at most
    `max_burst_secs` of any burst. Place it directly after the transport input.
    """

    def __init__(
        self,
        max_burst_secs: float = 0.2,
        clock: Callable[[], float] = time.monotonic,
        session: VoiceSession | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._session = session
        self._capacity = max_burst_secs
        self._clock = clock
        self._tokens = max_burst_secs
        self._last_refill: float | None = None
        self._dropping_secs = 0.0
        self.total_dropped_secs = 0.0

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame) and frame.sample_rate > 0:
            duration = frame.num_frames / frame.sample_rate
            now = self._clock()
            if self._last_refill is not None:
                self._tokens = min(self._capacity, self._tokens + (now - self._last_refill))
            self._last_refill = now

            # Small epsilon so exactly-real-time frames are never dropped by float rounding.
            if self._tokens + 1e-6 < duration:
                self._dropping_secs += duration
                self.total_dropped_secs += duration
                # A startup backlog is expected; drops once the call is live are a problem.
                if self._session is not None and self._session.ready_at is not None:
                    self._session.dropped_audio_secs = round(
                        self._session.dropped_audio_secs + duration, 2
                    )
                return
            self._tokens -= duration
            if self._dropping_secs:
                logger.info("Dropped {:.2f}s of stale (backlogged) audio", self._dropping_secs)
                self._dropping_secs = 0.0

        await self.push_frame(frame, direction)


class SessionRecorder(FrameProcessor):
    """Record readiness and STT latency on the session.

    - Ready: the `StartFrame` only reaches this point after every service has
      finished setup (the Deepgram WebSocket is open), so the call is live.
    - Latency: the time from VAD end-of-speech (when the STT service is asked
      to finalize) to the final transcript. If Deepgram's own endpointing
      delivers the final transcript before the VAD fires, no latency is
      recorded for that utterance, because it was not waiting on the VAD.

    The VAD runs in the user aggregator, which sends its speech events
    upstream to the STT service, so this processor sees them going upstream.
    The caller's words are recorded per turn by the user aggregator's
    `on_user_turn_stopped` event, not here.

    Place it directly after the STT service. All frames pass through unchanged.
    """

    def __init__(
        self, session: VoiceSession, turn_clock: "TurnClock | None" = None, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self._session = session
        self._turn_clock = turn_clock
        self._speech_stopped_at: float | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._session.mark_ready()
            logger.info(
                "Pipeline ready | session_id={} startup_ms={}",
                self._session.session_id,
                self._session.startup_ms,
            )
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self._speech_stopped_at = None
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._speech_stopped_at = time.monotonic()
            if self._turn_clock is not None:
                self._turn_clock.mark_speech_stopped()
        elif isinstance(frame, TranscriptionFrame) and frame.text.strip():
            latency_ms: float | None = None
            if self._speech_stopped_at is not None:
                latency_ms = round((time.monotonic() - self._speech_stopped_at) * 1000, 1)
                self._speech_stopped_at = None
                self._session.stt_latencies_ms.append(latency_ms)
            logger.info(
                "Final transcript | session_id={} stt_latency_ms={} chars={}",
                self._session.session_id,
                latency_ms,
                len(frame.text),
            )
            logger.debug("Caller said: {!r}", frame.text)

        await self.push_frame(frame, direction)


class TurnClock:
    """When the caller's latest turn ended. Shared by the reply latency probes.

    `mark_user_turn_end()` is called from the user aggregator's
    `on_user_turn_stopped` event. Each turn gets a new number, so each probe
    measures every turn once.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.turn = 0
        self.ended_at: float | None = None
        # When the VAD last heard the caller stop (set by SessionRecorder).
        self.speech_stopped_at: float | None = None

    def mark_speech_stopped(self) -> None:
        self.speech_stopped_at = self.clock()

    def mark_user_turn_end(self) -> float | None:
        """Close the caller's turn. Returns the turn-detection time in ms: from the VAD
        hearing silence to the turn ending (smart-turn + pause window + final transcript)."""
        self.turn += 1
        self.ended_at = self.clock()
        if self.speech_stopped_at is None:
            return None
        detect_ms = round((self.ended_at - self.speech_stopped_at) * 1000, 1)
        self.speech_stopped_at = None
        return detect_ms


class ReplyLatencyProbe(FrameProcessor):
    """Record the time from the end of the caller's turn to the first reply frame.

    Two probes are used: after the LLM (first non-empty `LLMTextFrame`, i.e.
    the first word of text) and after the TTS (first `TTSAudioRawFrame`, i.e.
    the moment the caller starts hearing the reply). Tool calls made before the
    reply are included, as the caller experiences them. Frames pass unchanged.
    """

    def __init__(
        self,
        turn_clock: TurnClock,
        latencies_ms: list[float],
        label: str,
        matches: Callable[[Frame], bool],
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._turn_clock = turn_clock
        self._latencies_ms = latencies_ms
        self._label = label
        self._matches = matches
        self._measured_turn = 0

    @property
    def replied_to_current_turn(self) -> bool:
        """Whether a matching frame has passed since the caller's latest turn ended."""
        return self._measured_turn == self._turn_clock.turn

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        clock = self._turn_clock
        if (
            clock.ended_at is not None
            and clock.turn != self._measured_turn
            and self._matches(frame)
        ):
            self._measured_turn = clock.turn
            latency_ms = round((clock.clock() - clock.ended_at) * 1000, 1)
            self._latencies_ms.append(latency_ms)
            logger.info("Reply latency | {}_ms={}", self._label, latency_ms)

        await self.push_frame(frame, direction)


def is_text(frame: Frame) -> bool:
    return isinstance(frame, LLMTextFrame) and bool(frame.text.strip())


def is_audio(frame: Frame) -> bool:
    return isinstance(frame, TTSAudioRawFrame)
