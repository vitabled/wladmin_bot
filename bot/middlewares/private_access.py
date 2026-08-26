"""Private-chat access gate: only allowlisted users may talk to the bot in ЛС.

Group chats are never restricted — moderation must keep working for everyone.
An empty allowlist disables the gate entirely (legacy behavior).
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
    """Silently drop private-chat events from users outside the allowlist.

    Registered as the outermost middleware so unlisted users never reach
    downstream middlewares (DB session, admin checks) or handlers.
    """

    def __init__(self, allowed_dm_ids: tuple[int, ...] = ()) -> None:
        self.allowed_dm_ids = frozenset(allowed_dm_ids)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat = data.get("event_chat") or extract_chat(event)
        # Groups always pass; an empty allowlist means "no restriction".
        if is_group(chat) or not self.allowed_dm_ids:
            return await handler(event, data)

        # Private chat with an active allowlist: require a known user.
        user = data.get("event_user") or extract_user(event)
        if user is None or user.id not in self.allowed_dm_ids:
            logger.info(
                "dm.blocked",
                extra={
                    "user_id": user.id if user is not None else None,
                    "chat_id": chat.id if chat is not None else None,
                },
            )
            return None

        return await handler(event, data)
