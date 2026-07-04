"""Configuration module using pydantic-settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Telegram ---
    telegram_bot_token: str

    # --- Database ---
    database_url: str = "sqlite+aiosqlite:///./content_zavod.db"

    # --- AI image provider selection: "openai" or "kie" ---
    # Controls which backend generates images. Text generation always uses the
    # OpenAI-compatible client below (point openai_base_url at any compatible API).
    ai_provider: Literal["openai", "kie"] = "openai"

    # --- OpenAI (text generation + voice transcription + image description) ---
    # Required. Used for text always, for images when ai_provider="openai",
    # and for Whisper/Vision regardless of the image provider.
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"

    # Text generation model (any chat-completions model on openai_base_url)
    llm_model: str = "gpt-4o"
    llm_max_tokens: int = 2000
    llm_temperature: float = 0.7

    # Voice transcription + image understanding (always via OpenAI)
    openai_transcribe_model: str = "whisper-1"
    openai_vision_model: str = "gpt-4o-mini"

    # OpenAI image model (used when ai_provider="openai"): "gpt-image-1" or "dall-e-3"
    openai_image_model: str = "gpt-image-1"

    # --- Kie.ai (image generation, used when ai_provider="kie") ---
    kie_api_key: str = ""
    kie_base_url: str = "https://api.kie.ai"
    # Kie.ai image model slug, e.g. "google/nano-banana", "flux/dev", etc.
    kie_image_model: str = "google/nano-banana"

    # Image aspect ratio / size (applies to both providers)
    image_size: Literal["1024x1024", "1792x1024", "1024x1792"] = "1024x1024"

    # --- Timezone ---
    default_timezone: str = "Europe/Moscow"

    # --- Scheduler / parser tuning (seconds) ---
    scheduler_interval: int = 300
    parser_delay: int = 2

    @property
    def is_sqlite(self) -> bool:
        """Check if using SQLite database."""
        return "sqlite" in self.database_url.lower()


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Global settings instance
settings = get_settings()
