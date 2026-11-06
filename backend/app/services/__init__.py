"""External AI service integrations (STT, LLM, TTS), one module each."""

from app.config import Settings


class ServiceNotConfiguredError(RuntimeError):
    """A required integration has no credentials configured."""


def missing_voice_credentials(settings: Settings) -> list[str]:
    """Names of the integrations the voice pipeline needs but has no key for.

    Grows as milestones add services (Groq in Milestone 3, ElevenLabs in Milestone 4).
    """
    missing: list[str] = []
    if settings.deepgram_api_key is None or not settings.deepgram_api_key.get_secret_value():
        missing.append("Deepgram")
    return missing
