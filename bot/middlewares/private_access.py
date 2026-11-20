"""Legacy private-chat gate, now permitting every direct message.

Group chats continue through unchanged. The allowlist argument remains accepted
so existing dispatcher wiring is backward-compatible while role filters protect
staff-only surfaces.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.middlewares.base import extract_chat, extract_user, is_group

logger = logging.getLogger(__name__)


class PrivateAccessMiddleware(BaseMiddleware):
    """Pass every event while recording public private-chat access."""

    def __init__(self, allowed_dm_ids: tuple[int, ...] = ()) -> None:
        self.allowed_dm_ids = frozenset(allowed_dm_ids)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat = data.get("event_chat") or extract_chat(event)
        if is_group(chat):
            return await handler(event, data)

        user = data.get("event_user") or extract_user(event)
        logger.info(
            "dm.public",
            extra={
                "user_id": user.id if user is not None else None,
                "chat_id": chat.id if chat is not None else None,
            },
        )

        return await handler(event, data)
