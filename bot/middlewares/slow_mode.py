"""Slow-mode middleware: drop messages that violate the chat's slow mode.

Registered innermost (after AdminMiddleware) and only on ``dp.message``:
edited messages and callbacks are never rate-limited. A blocked message is
already deleted and replied to by ``check_and_record`` — the middleware just
drops the event so no handler runs. Any unexpected error is logged and the
pipeline proceeds: slow mode must never break the bot.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.services.slow_mode import check_and_record

logger = logging.getLogger(__name__)


class SlowModeMiddleware(BaseMiddleware):
    """Enforce per-chat slow mode on incoming group messages."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            allowed = await check_and_record(data["bot"], event, data)
        except Exception:
            logger.warning("slow_mode.error", exc_info=True)
            return await handler(event, data)
        if not allowed:
            return None
        return await handler(event, data)
