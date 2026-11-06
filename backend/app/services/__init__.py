"""External AI service integrations (STT, LLM, TTS), one module each."""

from pydantic import SecretStr

from app.config import Settings


class ServiceNotConfiguredError(RuntimeError):
    """A required integration has no credentials configured."""


def missing_voice_credentials(settings: Settings) -> list[str]:
    """Names of the integrations the voice pipeline needs but has no key for.

    Grows as milestones add services (ElevenLabs in Milestone 4).
    """
    missing: list[str] = []
    if not has_secret(settings.deepgram_api_key):
        missing.append("Deepgram")
    if not has_secret(settings.groq_api_key):
        missing.append("Groq")
    return missing


def has_secret(value: SecretStr | None) -> bool:
    return value is not None and bool(value.get_secret_value())
