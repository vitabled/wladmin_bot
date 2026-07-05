"""Admin-detection middleware: inject is_admin/is_owner into data.

Статус админа кэшируется в Redis (короткий TTL), чтобы антиспам не дёргал
``get_chat_member`` на каждое сообщение. Деструктивные хендлеры делают свежую
перепроверку актора (кейс «отозвали админку между кэшем и действием»).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject

from bot.cache.redis import RedisClient
from bot.middlewares.base import extract_chat, extract_user, is_group
from bot.utils.telegram import get_chat_member_status

logger = logging.getLogger(__name__)

# Telegram's service account id used when an anonymous admin posts.
_ANON_BOT_ID = 1087968824


class AdminMiddleware(BaseMiddleware):
    """Compute whether the acting user is an admin/owner of the chat."""

    def __init__(self, redis: RedisClient, owner_id: int, cache_ttl: int = 300) -> None:
        self.redis = redis
        self.owner_id = owner_id
        self.cache_ttl = cache_ttl

    async def _is_admin_cached(self, bot: Bot, chat_id: int, user_id: int) -> bool:
        key = f"admin:{chat_id}:{user_id}"
        cached = await self.redis.get(key)
        if cached is not None:
            return cached == "1"
        status = await get_chat_member_status(bot, chat_id, user_id)
        is_adm = status in ("administrator", "creator")
        await self.redis.set(key, "1" if is_adm else "0", ttl=self.cache_ttl)
        return is_adm

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat = data.get("event_chat") or extract_chat(event)
        user = data.get("event_user") or extract_user(event)
        bot: Bot = data["bot"]

        is_owner = bool(user is not None and user.id == self.owner_id)
        is_admin = is_owner

        if not is_admin and is_group(chat) and user is not None:
            sender_chat = getattr(event, "sender_chat", None)
            if sender_chat is not None and sender_chat.id == chat.id:
                # Anonymous admin posting as the group itself.
                is_admin = True
            elif user.is_bot or user.id == _ANON_BOT_ID:
                is_admin = False
            else:
                is_admin = await self._is_admin_cached(bot, chat.id, user.id)

        data["is_owner"] = is_owner
        data["is_admin"] = is_admin
        data["owner_id"] = self.owner_id
        return await handler(event, data)
