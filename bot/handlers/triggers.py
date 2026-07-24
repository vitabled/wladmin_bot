"""Custom trigger auto-replies (Phase 3).

Вызывается из единственного per-message хендлера (роутер antispam) уже после
антиспама — отвечаем только на «выжившие» сообщения. Список триггеров кэшируется
в Redis (как стоп-слова), инвалидируется при изменении.
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import types
from sqlalchemy.ext.asyncio import AsyncSession

from bot.cache.redis import RedisClient
from bot.db import crud
from bot.services.triggers import TriggerService
from bot.utils.telegram import safe_send_message
from bot.utils.text import render_welcome

logger = logging.getLogger(__name__)


async def maybe_reply(message: types.Message, data: dict[str, Any]) -> bool:
    """Send a trigger's reply if the message matches one. Returns True if sent."""
    settings = data.get("settings")
    if not settings or not settings.get("triggers_enabled"):
        return False
    # Don't auto-reply to channel posts / anonymous admins / auto-forwards.
    if message.sender_chat is not None or message.is_automatic_forward:
        return False

    text = message.text or message.caption or ""
    if not text:
        return False

    redis: RedisClient = data["redis"]
    session: AsyncSession = data["session"]
    triggers = await redis.get_cached_triggers(message.chat.id)
    if triggers is None:
        triggers = await crud.list_triggers(session, message.chat.id)
        await redis.cache_triggers(message.chat.id, triggers)
    if not triggers:
        return False

    reply = TriggerService.find_reply(text, triggers)
    if not reply:
        return False

    user = message.from_user
    rendered = render_welcome(
        reply,
        first_name=(user.first_name if user else "") or "",
        user_id=user.id if user else 0,
        username=user.username if user else None,
        chat_title=message.chat.title or "",
        members_count=0,
    )
    await safe_send_message(message.bot, message.chat.id, rendered, parse_mode="HTML")
    return True
