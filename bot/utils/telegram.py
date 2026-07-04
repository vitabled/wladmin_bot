"""Telegram Bot API utilities and safety wrappers."""

import logging
from typing import Callable, Optional, TypeVar

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def _safe_api_call(
    coro,
    error_context: str,
    log_level: str = "warning",
) -> Optional[T]:
    """Generic wrapper for safe Telegram API calls with consistent error handling."""
    try:
        return await coro
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        log_fn = getattr(logger, log_level)
        log_fn(f"{error_context}: {e}")
        return None


async def safe_restrict_member(
    bot: Bot,
    chat_id: int,
    user_id: int,
    can_send_messages: bool = True,
    can_send_media_messages: bool = True,
    can_send_polls: bool = True,
    can_add_web_page_previews: bool = True,
) -> bool:
    """Safely restrict user permissions."""
    from aiogram.types import ChatPermissions

    permissions = ChatPermissions(
        can_send_messages=can_send_messages,
        can_send_media_messages=can_send_media_messages,
        can_send_polls=can_send_polls,
        can_add_web_page_previews=can_add_web_page_previews,
    )
    result = await _safe_api_call(
        bot.restrict_chat_member(chat_id, user_id, permissions),
        f"Cannot restrict user {user_id} in {chat_id}",
    )
    return result is not None


async def safe_ban_member(
    bot: Bot, chat_id: int, user_id: int
) -> bool:
    """Safely ban user from chat."""
    result = await _safe_api_call(
        bot.ban_chat_member(chat_id, user_id),
        f"Cannot ban user {user_id} in {chat_id}",
    )
    return result is not None


async def safe_unban_member(
    bot: Bot, chat_id: int, user_id: int
) -> bool:
    """Safely unban user from chat."""
    result = await _safe_api_call(
        bot.unban_chat_member(chat_id, user_id),
        f"Cannot unban user {user_id} in {chat_id}",
    )
    return result is not None


async def safe_kick_member(
    bot: Bot, chat_id: int, user_id: int
) -> bool:
    """Safely kick user (ban + unban)."""
    if not await safe_ban_member(bot, chat_id, user_id):
        return False

    result = await _safe_api_call(
        bot.unban_chat_member(chat_id, user_id),
        f"Cannot unban user {user_id} in {chat_id}",
    )
    return result is not None


async def safe_delete_message(
    bot: Bot, chat_id: int, message_id: int
) -> bool:
    """Safely delete message."""
    result = await _safe_api_call(
        bot.delete_message(chat_id, message_id),
        f"Cannot delete message {message_id} in {chat_id}",
        log_level="debug",
    )
    return result is not None


async def is_bot_admin(
    bot: Bot, chat_id: int, bot_id: int
) -> bool:
    """Check if bot is admin in chat."""
    try:
        member = await bot.get_chat_member(chat_id, bot_id)
        return member.is_admin
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logger.warning(f"Cannot check bot status in {chat_id}: {e}")
        return False


async def get_chat_member_status(
    bot: Bot, chat_id: int, user_id: int
) -> Optional[str]:
    """Get user status in chat."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logger.warning(f"Cannot get member {user_id} status in {chat_id}: {e}")
        return None
