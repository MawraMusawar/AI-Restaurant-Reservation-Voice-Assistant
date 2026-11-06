"""Custom Pipecat frame processors used by the voice pipeline.

Milestone 1 only: these provide an audio echo so the WebRTC path can be
verified end to end without any paid API. Deepgram STT, the Groq LLM and
ElevenLabs TTS replace them in Milestones 2-5.
"""

from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class AudioLoopback(FrameProcessor):
    """Send the caller's microphone audio straight back to them.

    Turns each `InputAudioRawFrame` into an `OutputAudioRawFrame`. The output
    transport resamples to its own sample rate when needed. Every other frame
    passes through unchanged.
    """

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            await self.push_frame(
                OutputAudioRawFrame(
                    audio=frame.audio,
                    sample_rate=frame.sample_rate,
                    num_channels=frame.num_channels,
                )
            )
        else:
            await self.push_frame(frame, direction)


class VADSpeakingBridge(FrameProcessor):
    """Convert raw VAD events into user speaking events.

    The browser client only understands `user-started-speaking` and
    `user-stopped-speaking` messages, which come from `UserStarted/Stopped
    SpeakingFrame`. Without an LLM context aggregator nothing emits those
    frames, so this processor derives them from Silero VAD. Milestone 5's
    user aggregator and turn strategies replace it.
    """

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, VADUserStartedSpeakingFrame):
            await self.push_frame(UserStartedSpeakingFrame())
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            await self.push_frame(UserStoppedSpeakingFrame())
