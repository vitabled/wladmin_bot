"""Application settings via pydantic-settings + logging bootstrap."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from bot.logging_conf import configure_logging as _configure_logging


class Settings(BaseSettings):
    """Settings loaded from environment / .env, validated at startup."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- Telegram / webhook ---
    TELEGRAM_BOT_TOKEN: str
    WEBHOOK_URL: str
    WEBHOOK_SECRET: str
    WEBHOOK_HOST: str = "0.0.0.0"
    WEBHOOK_PORT: int = 8000
    WEBHOOK_PATH: str = "/webhook"

    # Delivery mode: "webhook" (default) or "polling" (long-polling via
    # getUpdates — for hosts without a public HTTPS webhook URL).
    BOT_MODE: str = "webhook"

    # --- Owner ---
    OWNER_ID: int

    # --- Database / cache ---
    DATABASE_URL: str
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Logging ---
    LOG_LEVEL: str = "info"
    LOG_FILE: str = "logs/app.log"

    # --- i18n ---
    DEFAULT_LANGUAGE: str = "ru"

    # --- Cache tuning ---
    SETTINGS_CACHE_TTL: int = Field(default=3600, ge=1)

    # --- Web dashboard (Phase 7) ---
    WEB_HOST: str = "0.0.0.0"
    WEB_PORT: int = 8080
    # Bot @username (no @) for the Telegram Login widget on the dashboard.
    WEB_BOT_USERNAME: str = ""
    # Signing key for the dashboard session cookie; falls back to WEBHOOK_SECRET.
    WEB_SESSION_SECRET: str = ""
    # LOCAL DEV ONLY: enable /dev-login to sign in as OWNER_ID without Telegram
    # (needed to try the dashboard without a domain). NEVER enable in production.
    WEB_DEV_LOGIN: bool = False

    @field_validator("WEBHOOK_PATH")
    @classmethod
    def _ensure_leading_slash(cls, v: str) -> str:
        return v if v.startswith("/") else f"/{v}"

    @field_validator("LOG_LEVEL")
    @classmethod
    def _normalize_level(cls, v: str) -> str:
        allowed = {"debug", "info", "warn", "warning", "error"}
        lv = v.strip().lower()
        if lv not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}, got {v!r}")
        return "warning" if lv == "warn" else lv


@lru_cache
def get_settings() -> Settings:
    """Return cached Settings; raises pydantic ValidationError if invalid."""
    return Settings()


def configure_logging(settings: Settings) -> None:
    """Configure structured logging from settings (thin wrapper)."""
    _configure_logging(level=settings.LOG_LEVEL, log_file=settings.LOG_FILE)
