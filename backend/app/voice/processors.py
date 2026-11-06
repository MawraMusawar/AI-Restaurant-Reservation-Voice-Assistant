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
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
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

    def __init__(self, session: VoiceSession, **kwargs) -> None:
        super().__init__(**kwargs)
        self._session = session
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


class ResponseLatencyRecorder(FrameProcessor):
    """Measure the time from the end of the caller's turn to the assistant's first text.

    `mark_user_turn_end()` is called from the user aggregator's
    `on_user_turn_stopped` event. The next non-empty `LLMTextFrame` closes the
    measurement, so tool calls made before the reply are included, as the
    caller experiences them. Place it directly after the LLM service.
    """

    def __init__(
        self, session: VoiceSession, clock: Callable[[], float] = time.monotonic, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self._session = session
        self._clock = clock
        self._turn_ended_at: float | None = None

    def mark_user_turn_end(self) -> None:
        self._turn_ended_at = self._clock()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if (
            isinstance(frame, LLMTextFrame)
            and self._turn_ended_at is not None
            and frame.text.strip()
        ):
            latency_ms = round((self._clock() - self._turn_ended_at) * 1000, 1)
            self._turn_ended_at = None
            self._session.response_latencies_ms.append(latency_ms)
            logger.info(
                "Assistant first text | session_id={} response_latency_ms={}",
                self._session.session_id,
                latency_ms,
            )

        await self.push_frame(frame, direction)
