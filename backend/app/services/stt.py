"""Deepgram streaming speech-to-text.

Pipecat's DeepgramSTTService already provides what the live call needs:
- a streaming WebSocket with interim and final results,
- reconnection with exponential backoff, plus keepalives,
- an immediate "finalize" when the VAD detects the caller stopped speaking,
  which returns the final transcript without waiting for Deepgram's own endpointing,
- a permanent error (the processor becomes unusable) on 4xx responses,
  such as an invalid key or an exhausted quota.
"""

from pipecat.services.deepgram.stt import DeepgramSTTService

from app.config import Settings
from app.services import ServiceNotConfiguredError

# Words that matter for a reservation call. Nova-3 keyterm prompting improves
# recognition of these without changing anything else.
RESTAURANT_KEYTERMS = ["reservation", "table", "booking", "party of"]


def create_stt_service(settings: Settings) -> DeepgramSTTService:
    if settings.deepgram_api_key is None or not settings.deepgram_api_key.get_secret_value():
        raise ServiceNotConfiguredError("DEEPGRAM_API_KEY is not set")

    options = DeepgramSTTService.Settings(
        model=settings.deepgram_model,
        language=settings.deepgram_language,
        interim_results=True,
        punctuate=True,
        smart_format=True,
    )
    if settings.deepgram_model.startswith("nova-3"):
        options.keyterm = RESTAURANT_KEYTERMS  # keyterm prompting is a Nova-3 feature

    return DeepgramSTTService(
        api_key=settings.deepgram_api_key.get_secret_value(),
        base_url=settings.deepgram_base_url,
        settings=options,
    )
