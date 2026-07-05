"""Chat-settings middleware: auto-register chat, load settings (cache→DB)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.cache.redis import RedisClient
from bot.db import crud
from bot.middlewares.base import extract_chat, extract_user, is_group

logger = logging.getLogger(__name__)


class SettingsMiddleware(BaseMiddleware):
    """Populate ``data['settings']`` (dict) and ``data['chat_language']``.

    For group chats: upsert the Chat row (auto-registration), upsert the acting
    user (so @username targeting works later), and load settings from Redis with
    a DB fallback. Private chats get ``settings=None``.
    """

    def __init__(self, redis: RedisClient, cache_ttl: int = 3600) -> None:
        self.redis = redis
        self.cache_ttl = cache_ttl

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat = extract_chat(event)
        user = extract_user(event)
        data["event_chat"] = chat
        data["event_user"] = user

        if is_group(chat):
            session = data["session"]
            chat_row = await crud.ensure_chat(
                session, chat.id, chat.title or "", chat.type
            )
            data["chat_language"] = chat_row.language

            cached = await self.redis.get_cached_settings(chat.id)
            if cached is None:
                settings_obj = await crud.get_or_create_settings(session, chat.id)
                cached = crud.settings_to_dict(settings_obj)
                await self.redis.cache_settings(chat.id, cached, ttl=self.cache_ttl)
            data["settings"] = cached

            if user is not None and not user.is_bot:
                await crud.upsert_user(session, user.id, user.first_name, user.username)
        else:
            data["settings"] = None
            data["chat_language"] = None

        return await handler(event, data)
