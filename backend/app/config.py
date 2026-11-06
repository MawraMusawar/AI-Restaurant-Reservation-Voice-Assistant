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

    # --- Speech-to-Text (Milestone 2) ---
    deepgram_api_key: SecretStr | None = None

    # --- LLM (Milestone 3) ---
    groq_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-120b"

    # --- Text-to-Speech (Milestone 4) ---
    elevenlabs_api_key: SecretStr | None = None
    elevenlabs_voice_id: str | None = None

    # --- Database (Milestone 6) ---
    supabase_url: str | None = None
    supabase_service_role_key: SecretStr | None = None
    database_url: SecretStr | None = None

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
