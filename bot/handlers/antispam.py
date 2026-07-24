"""Antispam: scan every group message / edit and act per chat settings."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot, Router, types
from sqlalchemy.ext.asyncio import AsyncSession

from bot.cache.redis import RedisClient
from bot.constants import ACTION_BAN, ACTION_MUTE, ACTION_WARN
from bot.db import crud
from bot.filters.chat_type import IsGroup
from bot.handlers import actions, antiflood
from bot.services.antispam import AntispamService
from bot.utils.telegram import safe_delete_message

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(IsGroup())


async def _process(message: types.Message, data: dict[str, Any]) -> None:
    settings = data.get("settings")
    if not settings:
        return

    # Don't moderate channel posts / anonymous admins / linked-channel
    # auto-forwards as spam.
    if message.sender_chat is not None or message.is_automatic_forward:
        return

    if not (
        settings["filter_links"]
        or settings["filter_forwards"]
        or settings["filter_stopwords"]
    ):
        return
    if settings.get("antispam_exempt_admins", True) and data.get("is_admin"):
        return

    bot: Bot = message.bot
    chat = message.chat
    user = message.from_user
    session: AsyncSession = data["session"]
    redis: RedisClient = data["redis"]

    stopwords: list[str] = []
    if settings["filter_stopwords"]:
        cached = await redis.get_cached_stopwords(chat.id)
        if cached is None:
            cached = await crud.list_stopwords(session, chat.id)
            await redis.cache_stopwords(chat.id, cached)
        stopwords = cached

    text = message.text or message.caption or ""
    is_spam, reason = AntispamService.check_message(
        text,
        forward_origin=message.forward_origin,
        stopwords=stopwords,
        filter_links=settings["filter_links"],
        filter_forwards=settings["filter_forwards"],
        filter_stopwords=settings["filter_stopwords"],
    )
    if not is_spam or reason is None:
        return

    await safe_delete_message(bot, chat.id, message.message_id)
    if user is None:
        return

    reason_kind = reason.split(":", 1)[0]
    await crud.add_mod_log(
        session, chat.id, bot.id, user.id, f"antispam_{reason_kind}", reason
    )

    action = settings["antispam_action"]
    if action == ACTION_WARN:
        await actions.do_warn(
            bot,
            session,
            chat.id,
            bot.id,
            user.id,
            f"antispam:{reason_kind}",
            settings,
        )
    elif action == ACTION_MUTE:
        await actions.do_mute(
            bot,
            session,
            chat.id,
            bot.id,
            user.id,
            None,
            f"antispam:{reason_kind}",
        )
    elif action == ACTION_BAN:
        await actions.do_ban(
            bot,
            session,
            chat.id,
            bot.id,
            user.id,
            None,
            f"antispam:{reason_kind}",
        )
    # action == "delete": message already removed above.


def _guards_applicable(message: types.Message, data: dict[str, Any]) -> bool:
    """Phase 2 guards run for non-admin, real user messages in a group."""
    if data.get("is_admin"):
        return False
    if not data.get("settings"):
        return False
    return message.sender_chat is None and not message.is_automatic_forward


@router.message()
async def on_message(message: types.Message, **data: Any) -> None:
    if _guards_applicable(message, data):
        if await antiflood.enforce_newbie_media(message, data):
            return
        if await antiflood.enforce_flood(message, data):
            return
    await _process(message, data)


@router.edited_message()
async def on_edited_message(message: types.Message, **data: Any) -> None:
    # Newbie media applies to edits too (join → text → edit-in-media bypass);
    # flood counting does not — editing an old message isn't flooding.
    if _guards_applicable(message, data) and await antiflood.enforce_newbie_media(
        message, data
    ):
        return
    await _process(message, data)
