"""Telegram Bot API utilities and safety wrappers."""

import logging
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

logger = logging.getLogger(__name__)


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
    try:
        from aiogram.types import ChatPermissions

        permissions = ChatPermissions(
            can_send_messages=can_send_messages,
            can_send_media_messages=can_send_media_messages,
            can_send_polls=can_send_polls,
            can_add_web_page_previews=can_add_web_page_previews,
        )
        await bot.restrict_chat_member(chat_id, user_id, permissions)
        return True
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logger.warning(f"Cannot restrict user {user_id} in {chat_id}: {e}")
        return False


async def safe_ban_member(
    bot: Bot, chat_id: int, user_id: int
) -> bool:
    """Safely ban user from chat."""
    try:
        await bot.ban_chat_member(chat_id, user_id)
        return True
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logger.warning(f"Cannot ban user {user_id} in {chat_id}: {e}")
        return False


async def safe_unban_member(
    bot: Bot, chat_id: int, user_id: int
) -> bool:
    """Safely unban user from chat."""
    try:
        await bot.unban_chat_member(chat_id, user_id)
        return True
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logger.warning(f"Cannot unban user {user_id} in {chat_id}: {e}")
        return False


async def safe_kick_member(
    bot: Bot, chat_id: int, user_id: int
) -> bool:
    """Safely kick user (ban + unban)."""
    if not await safe_ban_member(bot, chat_id, user_id):
        return False

    try:
        await bot.unban_chat_member(chat_id, user_id)
        return True
    except Exception as e:
        logger.warning(f"Cannot unban user {user_id} in {chat_id}: {e}")
        return False


async def safe_delete_message(
    bot: Bot, chat_id: int, message_id: int
) -> bool:
    """Safely delete message."""
    try:
        await bot.delete_message(chat_id, message_id)
        return True
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logger.debug(f"Cannot delete message {message_id} in {chat_id}: {e}")
        return False


async def is_bot_admin(
    bot: Bot, chat_id: int, bot_id: int
) -> bool:
    """Check if bot is admin in chat."""
    try:
        member = await bot.get_chat_member(chat_id, bot_id)
        return member.is_admin
    except Exception as e:
        logger.warning(f"Cannot check bot status in {chat_id}: {e}")
        return False


async def get_chat_member_status(
    bot: Bot, chat_id: int, user_id: int
) -> Optional[str]:
    """Get user status in chat."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status
    except Exception as e:
        logger.warning(f"Cannot get member {user_id} status in {chat_id}: {e}")
        return None
