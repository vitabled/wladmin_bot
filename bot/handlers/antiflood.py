"""Per-message guards: anti-flood + newbie media restriction (Phase 2).

Эти проверки вызываются из единственного catch-all message-хендлера
(роутер antispam), а не из отдельного роутера: два catch-all роутера каждый
«поглотили» бы апдейт и не дали сработать остальным. Каждая функция
возвращает ``True``, если сообщение обработано (удалено/наказано) и дальнейшую
модерацию нужно прекратить.
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot, types
from sqlalchemy.ext.asyncio import AsyncSession

from bot.cache.redis import RedisClient
from bot.constants import ACTION_BAN, ACTION_KICK, ANTIFLOOD_MUTE_SECONDS
from bot.db import crud
from bot.handlers import actions
from bot.services.antiflood import AntifloodService
from bot.utils.telegram import safe_delete_message

logger = logging.getLogger(__name__)


async def enforce_newbie_media(message: types.Message, data: dict[str, Any]) -> bool:
    """Delete media from users still within their newbie probation window."""
    settings = data.get("settings")
    if not settings or not settings.get("newbie_media_enabled"):
        return False
    user = message.from_user
    if user is None:
        return False
    if not AntifloodService.is_restricted_media(message.content_type):
        return False

    redis: RedisClient = data["redis"]
    if not await redis.is_newbie(message.chat.id, user.id):
        return False

    bot: Bot = message.bot
    await safe_delete_message(bot, message.chat.id, message.message_id)
    session: AsyncSession = data["session"]
    await crud.add_mod_log(
        session,
        message.chat.id,
        bot.id,
        user.id,
        "newbie_media",
        str(message.content_type),
    )
    return True


async def enforce_flood(message: types.Message, data: dict[str, Any]) -> bool:
    """Count messages per window; act on the message that trips the limit."""
    settings = data.get("settings")
    if not settings or not settings.get("antiflood_enabled"):
        return False
    user = message.from_user
    if user is None:
        return False

    redis: RedisClient = data["redis"]
    window = int(settings.get("antiflood_window") or 5)
    limit = int(settings.get("antiflood_limit") or 5)
    count = await redis.bump_flood(message.chat.id, user.id, window)
    if not AntifloodService.is_flood(count, limit):
        return False

    # Reset so we act once per burst rather than on every message past the cap.
    await redis.reset_flood(message.chat.id, user.id)

    bot: Bot = message.bot
    session: AsyncSession = data["session"]
    await safe_delete_message(bot, message.chat.id, message.message_id)

    action = settings.get("antiflood_action", "mute")
    if action == ACTION_BAN:
        await actions.do_ban(
            bot, session, message.chat.id, bot.id, user.id, None, "antiflood"
        )
    elif action == ACTION_KICK:
        await actions.do_kick(
            bot, session, message.chat.id, bot.id, user.id, "antiflood"
        )
    else:  # default: temporary mute
        await actions.do_mute(
            bot,
            session,
            message.chat.id,
            bot.id,
            user.id,
            ANTIFLOOD_MUTE_SECONDS,
            "antiflood",
        )
    return True
