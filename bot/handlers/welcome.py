"""Welcome messages + service-message cleanup."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from aiogram import Bot, F, Router, types

from bot.filters.chat_type import IsGroup
from bot.utils.tasks import spawn
from bot.utils.telegram import safe_delete_message, safe_send_message
from bot.utils.text import render_welcome

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(IsGroup())


async def _safe_member_count(bot: Bot, chat_id: int) -> int:
    try:
        return await bot.get_chat_member_count(chat_id)
    except Exception:
        return 0


async def _delete_after(bot: Bot, chat_id: int, message_id: int, delay: int) -> None:
    await asyncio.sleep(delay)
    await safe_delete_message(bot, chat_id, message_id)


async def send_welcome(
    bot: Bot,
    chat: types.Chat,
    user: types.User,
    settings: dict[str, Any],
    translator: Callable[..., str],
) -> types.Message | None:
    """Send the (optionally auto-deleted) welcome message for a joined user."""
    if not settings or not settings.get("welcome_enabled"):
        return None

    members = await _safe_member_count(bot, chat.id)
    template = settings.get("welcome_text") or translator("welcome_default")
    text = render_welcome(
        template,
        first_name=user.first_name or "",
        user_id=user.id,
        username=user.username,
        chat_title=chat.title or "",
        members_count=members,
    )
    msg = await safe_send_message(bot, chat.id, text, parse_mode="HTML")

    delete_after = settings.get("delete_welcome_after")
    if msg is not None and delete_after:
        spawn(_delete_after(bot, chat.id, msg.message_id, int(delete_after)))
    return msg


@router.message(F.left_chat_member)
async def on_member_left(message: types.Message, **data: Any) -> None:
    """Delete the 'user left' service message when configured."""
    settings = data.get("settings")
    if settings and settings.get("delete_service_messages"):
        await safe_delete_message(message.bot, message.chat.id, message.message_id)
