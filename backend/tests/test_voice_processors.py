from pipecat.frames.frames import (
    InputAudioRawFrame,
    SpeechOutputAudioRawFrame,
    TextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.tests.utils import run_test

from app.voice.processors import AudioLoopback, VADSpeakingBridge


async def test_audio_loopback_turns_input_audio_into_output_audio() -> None:
    audio = b"\x01\x00" * 160  # 10 ms of 16 kHz mono PCM
    down, _ = await run_test(
        AudioLoopback(),
        frames_to_send=[InputAudioRawFrame(audio=audio, sample_rate=16000, num_channels=1)],
        # Must be the speech-stream subclass so the output transport emits bot-speaking events.
        expected_down_frames=[SpeechOutputAudioRawFrame],
    )
    echoed = down[0]
    assert isinstance(echoed, SpeechOutputAudioRawFrame)
    assert echoed.audio == audio
    assert echoed.sample_rate == 16000
    assert echoed.num_channels == 1


async def test_audio_loopback_passes_other_frames_through() -> None:
    await run_test(
        AudioLoopback(),
        frames_to_send=[TextFrame(text="hello")],
        expected_down_frames=[TextFrame],
    )


async def test_vad_bridge_emits_user_speaking_frames() -> None:
    await run_test(
        VADSpeakingBridge(),
        frames_to_send=[VADUserStartedSpeakingFrame(), VADUserStoppedSpeakingFrame()],
        expected_down_frames=[
            VADUserStartedSpeakingFrame,
            UserStartedSpeakingFrame,
            VADUserStoppedSpeakingFrame,
            UserStoppedSpeakingFrame,
        ],
    )
