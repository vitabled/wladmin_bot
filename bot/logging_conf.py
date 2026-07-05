"""Structured JSON logging with secret redaction and size-based rotation.

Никогда не логируем секреты: bot-токен, `Authorization`/`Cookie`, пароли,
api-ключи, webhook-secret. Редактор гонится по regex до сериализации.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterable
from logging.handlers import RotatingFileHandler
from typing import Any, Final

_MAX_BYTES: Final = 10 * 1024 * 1024  # 10 MB
_BACKUP_COUNT: Final = 5

# key=value / "key": "value" style secret leaks.
_SECRET_KEY_RE: Final = re.compile(
    r"(?i)(token|secret|password|passwd|pwd|api[_-]?key|authorization|cookie|"
    r"session[_-]?id|access[_-]?token|refresh[_-]?token)"
    r"(\"?\s*[:=]\s*\"?)([^\s\"',;)]+)"
)
# Telegram bot token: <digits>:<35+ base64url chars>.
_BOT_TOKEN_RE: Final = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")
# token=... / key=... inside URLs.
_URL_SECRET_RE: Final = re.compile(
    r"(?i)([?&](?:token|secret|api[_-]?key|access[_-]?token)=)([^&\s]+)"
)

_REDACTED: Final = "[REDACTED]"

# Standard LogRecord attributes we must not treat as "extra" structured fields.
_RESERVED: Final = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)


def redact(text: str) -> str:
    """Mask secrets in an arbitrary string."""
    if not text:
        return text
    text = _BOT_TOKEN_RE.sub(_REDACTED, text)
    text = _SECRET_KEY_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", text)
    text = _URL_SECRET_RE.sub(lambda m: f"{m.group(1)}{_REDACTED}", text)
    return text


class RedactingJsonFormatter(logging.Formatter):
    """Render each record as a single redacted JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Structured extras: logger.info("evt", extra={"user_id": 1}).
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        line = json.dumps(payload, ensure_ascii=False, default=str)
        return redact(line)


def configure_logging(
    level: str = "info",
    log_file: str | None = "logs/app.log",
    *,
    extra_quiet: Iterable[str] = ("aiogram.event", "aiohttp.access"),
) -> None:
    """Configure root logging idempotently.

    * JSON lines to stderr always; to ``log_file`` with 10MB×5 rotation if set.
    * ``level`` from env (debug/info/warn/error), default info.
    * Secrets redacted by the formatter.
    """
    root = logging.getLogger()
    numeric = getattr(logging, level.upper(), logging.INFO)
    root.setLevel(numeric)

    # Idempotent: drop handlers we installed on a previous call.
    for handler in list(root.handlers):
        if getattr(handler, "_bot_managed", False):
            root.removeHandler(handler)

    formatter = RedactingJsonFormatter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console._bot_managed = True  # type: ignore[attr-defined]
    root.addHandler(console)

    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        try:
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            file_handler._bot_managed = True  # type: ignore[attr-defined]
            root.addHandler(file_handler)
        except OSError:
            # Never let logging setup crash the app; stderr already attached.
            root.warning("Could not open log file %s, using stderr only", log_file)

    for noisy in extra_quiet:
        logging.getLogger(noisy).setLevel(max(numeric, logging.WARNING))
