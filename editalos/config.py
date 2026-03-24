from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = (ROOT_DIR / "editalos.db").resolve()
DEFAULT_DB_URL = f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "EditalOS"
    db_url: str = Field(default=DEFAULT_DB_URL, alias="EDITALOS_DB_URL")
    app_timezone: str = Field(default="America/Sao_Paulo", alias="EDITALOS_APP_TIMEZONE")

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_text_model: str = Field(default="gpt-5.4", alias="OPENAI_TEXT_MODEL")
    openai_embedding_model: str = Field(
        default="text-embedding-3-small",
        alias="OPENAI_EMBEDDING_MODEL",
    )
    openai_service_tier: str = Field(default="default", alias="OPENAI_SERVICE_TIER")
    openai_flex_enabled: bool = Field(default=False, alias="OPENAI_FLEX_ENABLED")
    openai_prompt_cache_key_prefix: str = Field(
        default="EditalOS", alias="OPENAI_PROMPT_CACHE_KEY_PREFIX"
    )
    openai_prompt_cache_retention: str = Field(
        default="extended", alias="OPENAI_PROMPT_CACHE_RETENTION"
    )

    fsrs_desired_retention: float = Field(
        default=0.90,
        alias="FSRS_DESIRED_RETENTION",
        ge=0.5,
        le=0.99,
    )
    default_daily_minutes: int = Field(
        default=240,
        alias="EDITALOS_DEFAULT_DAILY_MINUTES",
        ge=30,
        le=960,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
