"""Application configuration loaded from environment variables / `.env`.

Secrets are typed as `SecretStr` so they are never printed in logs or reprs.
Integration keys are optional at this stage; each milestone that needs a key
validates its presence when the integration is first used.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application ---
    app_env: Literal["development", "test", "production"] = "development"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    # Comma-separated list of allowed browser origins.
    cors_origins: str = "http://localhost:3000"

    # --- Logging ---
    log_level: Literal["TRACE", "DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = False

    # --- Voice sessions ---
    # Max simultaneous calls. Kept low to protect free API quotas.
    voice_max_concurrent_sessions: int = Field(default=2, ge=1, le=20)

    # --- Speech-to-Text (Milestone 2) ---
    deepgram_api_key: SecretStr | None = None
    deepgram_model: str = "nova-3-general"
    # Empty = Deepgram's default (US). "api.eu.deepgram.com" connected ~2x faster
    # from South Asia in testing (0.7-1.7 s vs 1.8-4.4 s handshake).
    deepgram_base_url: str = ""
    # BCP-47 code, e.g. "en", "en-US", "en-IN"; "multi" for multilingual code-switching
    deepgram_language: str = "en"

    # --- LLM (Milestone 3) ---
    groq_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-120b"
    # Comma-separated models tried, in order, when the main model is rate-limited.
    # Each Groq model has its own free-tier budget (~8K tokens/min, 200K tokens/day).
    # Not gpt-oss-20b: in testing it invented caller details and answered for the caller.
    groq_fallback_models: str = "qwen/qwen3.8-27b"
    # "low" keeps time-to-first-token around 0.5-0.8 s on gpt-oss.
    groq_reasoning_effort: Literal["low", "medium", "high"] = "low"

    # --- Text-to-Speech (Milestone 4) ---
    elevenlabs_api_key: SecretStr | None = None
    elevenlabs_voice_id: str | None = None

    # --- Database (Milestone 6) ---
    supabase_url: str | None = None
    supabase_service_role_key: SecretStr | None = None
    database_url: SecretStr | None = None

    @property
    def groq_fallback_model_list(self) -> list[str]:
        return [m.strip() for m in self.groq_fallback_models.split(",") if m.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def integration_status(self) -> dict[str, bool]:
        """Which integrations have credentials configured (never the values)."""
        return {
            "deepgram": self.deepgram_api_key is not None,
            "groq": self.groq_api_key is not None,
            "elevenlabs": self.elevenlabs_api_key is not None,
            "database": self.database_url is not None,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
