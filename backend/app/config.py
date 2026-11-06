"""Application configuration loaded from environment variables / `.env`.

Secrets are typed as `SecretStr` so they are never printed in logs or reprs.
Integration keys are optional at this stage; each milestone that needs a key
validates its presence when the integration is first used.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# ElevenLabs premade voice "Sarah - Mature, Reassuring, Confident" (female, American).
DEFAULT_ELEVENLABS_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"


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
    # Turn-taking (Milestone 5). Continuous voice needed to start the caller's turn
    # (and interrupt the assistant): longer ignores coughs and "mm-hmm", shorter
    # stops the assistant sooner. Pipecat's default is 0.2 s. Measured (separate
    # processes): 0.2-0.3 s -> barge-in 0.6-0.9 s but a one-word "okay" interrupts;
    # 0.4 s -> "okay" ignored, barge-in 1.2-1.6 s for "Hi, I ...". Also limits the
    # assistant interrupting itself when its voice leaks from speakers into the mic.
    voice_vad_start_secs: float = Field(default=0.4, ge=0.1, le=1.0)
    # While the assistant speaks, transcribed words that also count as an interruption.
    voice_barge_in_min_words: int = Field(default=2, ge=1, le=5)
    # After the caller pauses, how long they may resume and stay in the same turn. The
    # turn ends when smart-turn judges the sentence complete AND this has passed.
    # Without it, "Great. That's all. Thanks. Goodbye." became 4 turns and 4 LLM
    # requests (3 cancelled, each ~2K tokens of the free per-minute budget).
    voice_turn_end_silence_secs: float = Field(default=0.4, ge=0.0, le=2.0)
    # Caller silent this long after the assistant spoke -> "Are you still there?"
    voice_silence_prompt_secs: float = Field(default=8.0, ge=2.0, le=60.0)

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
    # Empty = DEFAULT_ELEVENLABS_VOICE_ID. Premade voices work on the free plan.
    elevenlabs_voice_id: str = ""
    # Flash v2.5: lowest latency, and half the credits per character of v2/v3.
    elevenlabs_model: str = "eleven_flash_v2_5"

    # --- Database (Milestone 6) ---
    supabase_url: str | None = None
    supabase_service_role_key: SecretStr | None = None
    database_url: SecretStr | None = None

    @property
    def elevenlabs_voice(self) -> str:
        return self.elevenlabs_voice_id.strip() or DEFAULT_ELEVENLABS_VOICE_ID

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
