import logging
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Telegram
    TELEGRAM_BOT_TOKEN: str
    WEBHOOK_URL: str
    WEBHOOK_SECRET: str
    WEBHOOK_PORT: int = 8000
    WEBHOOK_HOST: str = "0.0.0.0"

    # Owner
    OWNER_ID: int

    # Database
    DATABASE_URL: str

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Logging
    LOG_LEVEL: str = "info"
    LOG_FILE: str = "logs/app.log"

    # i18n
    DEFAULT_LANGUAGE: str = "ru"

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()


def configure_logging(settings: Settings) -> None:
    """Configure structured logging."""
    import os
    import json
    from logging.handlers import RotatingFileHandler

    os.makedirs(os.path.dirname(settings.LOG_FILE), exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(settings.LOG_LEVEL.upper())

    formatter = logging.Formatter(
        '{"time": "%(asctime)s", "level": "%(levelname)s", "module": "%(name)s", "message": "%(message)s"}'
    )

    file_handler = RotatingFileHandler(
        settings.LOG_FILE, maxBytes=10_000_000, backupCount=5
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
