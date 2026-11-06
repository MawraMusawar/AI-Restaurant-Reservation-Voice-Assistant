"""Text-to-speech service factory (ElevenLabs, streaming over WebSocket).

`ElevenLabsTTSService` keeps one WebSocket open for the call and streams the
LLM's text in as it is generated, so playback starts after the first phrase,
not after the whole reply. It returns word timestamps, which drive the
`bot-output` messages that show the browser each word as it is spoken. On an
interruption the service closes the audio context and the output transport
drops queued audio, so the assistant stops talking at once.

Free plan (checked 2026-11-06): 10,000 credits a month. `eleven_flash_v2_5`
costs 0.5 credits per character, so about 20,000 spoken characters.
"""

from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

from app.config import Settings
from app.services import ServiceNotConfiguredError, has_secret


def create_tts_service(settings: Settings) -> ElevenLabsTTSService:
    if not has_secret(settings.elevenlabs_api_key):
        raise ServiceNotConfiguredError("ELEVENLABS_API_KEY is not set")
    assert settings.elevenlabs_api_key is not None
    return ElevenLabsTTSService(
        api_key=settings.elevenlabs_api_key.get_secret_value(),
        settings=ElevenLabsTTSService.Settings(
            voice=settings.elevenlabs_voice,
            model=settings.elevenlabs_model,
        ),
    )
