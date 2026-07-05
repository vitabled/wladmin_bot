"""Safe Telegram Bot API wrappers.

Все обёртки:
* ретраят flood-wait (429 ``TelegramRetryAfter``) с backoff и лимитом попыток;
* ретраят транзиентные сетевые ошибки с экспоненциальным backoff;
* глотают 400/403 (нет прав / уже применено / цель недоступна) → ``None``/``False``,
  чтобы бизнес-логика не падала на ожидаемых отказах Telegram.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import ChatPermissions, Message

logger = logging.getLogger(__name__)

_MAX_FLOOD_RETRIES = 5
_MAX_NETWORK_RETRIES = 3
_MAX_FLOOD_SLEEP = 60  # cap a hostile retry_after so we never hang forever


async def call_with_retry[T](
    factory: Callable[[], Awaitable[T]],
    error_context: str,
    *,
    log_level: str = "warning",
) -> T | None:
    """Invoke ``factory()`` (a fresh coroutine each try) with retry policy.

    Returns the result, or ``None`` if the call was rejected (400/403) or all
    retries were exhausted.
    """
    network_attempts = 0
    flood_attempts = 0
    while True:
        try:
            return await factory()
        except TelegramRetryAfter as e:
            flood_attempts += 1
            if flood_attempts > _MAX_FLOOD_RETRIES:
                logger.error("%s: flood-wait retries exhausted", error_context)
                return None
            delay = min(int(e.retry_after) + 1, _MAX_FLOOD_SLEEP)
            logger.warning(
                "%s: flood wait, retry in %ss (attempt %s)",
                error_context,
                delay,
                flood_attempts,
            )
            await asyncio.sleep(delay)
        except TelegramNetworkError as e:
            network_attempts += 1
            if network_attempts > _MAX_NETWORK_RETRIES:
                logger.error("%s: network retries exhausted: %s", error_context, e)
                return None
            await asyncio.sleep(2**network_attempts)
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            getattr(logger, log_level)("%s: %s", error_context, e)
            return None


def _permissions(*, can_send: bool) -> ChatPermissions:
    """Build a ChatPermissions object that mutes (all False) or unmutes."""
    return ChatPermissions(
        can_send_messages=can_send,
        can_send_audios=can_send,
        can_send_documents=can_send,
        can_send_photos=can_send,
        can_send_videos=can_send,
        can_send_video_notes=can_send,
        can_send_voice_notes=can_send,
        can_send_polls=can_send,
        can_send_other_messages=can_send,
        can_add_web_page_previews=can_send,
    )


async def safe_ban_member(
    bot: Bot,
    chat_id: int,
    user_id: int,
    until_date: datetime | None = None,
) -> bool:
    """Ban (optionally until ``until_date`` for a temporary ban)."""
    result = await call_with_retry(
        lambda: bot.ban_chat_member(chat_id, user_id, until_date=until_date),
        f"ban user={user_id} chat={chat_id}",
    )
    return result is not None


async def safe_unban_member(
    bot: Bot, chat_id: int, user_id: int, only_if_banned: bool = True
) -> bool:
    """Unban a user (idempotent — ``only_if_banned`` avoids re-adding them)."""
    result = await call_with_retry(
        lambda: bot.unban_chat_member(chat_id, user_id, only_if_banned=only_if_banned),
        f"unban user={user_id} chat={chat_id}",
    )
    return result is not None


async def safe_kick_member(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Kick = ban then immediately unban so the user can rejoin."""
    if not await safe_ban_member(bot, chat_id, user_id):
        return False
    return await safe_unban_member(bot, chat_id, user_id, only_if_banned=True)


async def safe_mute_member(
    bot: Bot,
    chat_id: int,
    user_id: int,
    until_date: datetime | None = None,
) -> bool:
    """Mute (restrict all sending), optionally until ``until_date``."""
    result = await call_with_retry(
        lambda: bot.restrict_chat_member(
            chat_id,
            user_id,
            permissions=_permissions(can_send=False),
            until_date=until_date,
        ),
        f"mute user={user_id} chat={chat_id}",
    )
    return result is not None


async def safe_unmute_member(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Lift a mute by restoring send permissions."""
    result = await call_with_retry(
        lambda: bot.restrict_chat_member(
            chat_id, user_id, permissions=_permissions(can_send=True)
        ),
        f"unmute user={user_id} chat={chat_id}",
    )
    return result is not None


async def safe_restrict_new_member(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Restrict a just-joined member (used before captcha is solved)."""
    return await safe_mute_member(bot, chat_id, user_id)


async def safe_delete_message(bot: Bot, chat_id: int, message_id: int) -> bool:
    """Delete a message; missing/old messages are a no-op (debug log)."""
    result = await call_with_retry(
        lambda: bot.delete_message(chat_id, message_id),
        f"delete msg={message_id} chat={chat_id}",
        log_level="debug",
    )
    return result is not None


async def safe_send_message(
    bot: Bot, chat_id: int, text: str, **kwargs: Any
) -> Message | None:
    """Send a message with retry; returns the Message or None on failure."""
    return await call_with_retry(
        lambda: bot.send_message(chat_id, text, **kwargs),
        f"send to chat={chat_id}",
    )


async def is_bot_admin(bot: Bot, chat_id: int, bot_id: int) -> bool:
    """Whether the bot itself is an administrator in the chat."""
    member = await call_with_retry(
        lambda: bot.get_chat_member(chat_id, bot_id),
        f"get bot status chat={chat_id}",
    )
    return member is not None and member.status in ("administrator", "creator")


async def bot_can_restrict(bot: Bot, chat_id: int, bot_id: int) -> bool:
    """Whether the bot has ``can_restrict_members`` in the chat."""
    member = await call_with_retry(
        lambda: bot.get_chat_member(chat_id, bot_id),
        f"get bot perms chat={chat_id}",
    )
    if member is None:
        return False
    if member.status == "creator":
        return True
    return bool(getattr(member, "can_restrict_members", False))


async def get_chat_member_status(bot: Bot, chat_id: int, user_id: int) -> str | None:
    """Return a user's membership status or None if it can't be fetched."""
    member = await call_with_retry(
        lambda: bot.get_chat_member(chat_id, user_id),
        f"get member={user_id} status chat={chat_id}",
    )
    return member.status if member is not None else None


async def is_user_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Fresh admin re-check (creator/administrator). Safe-deny on failure."""
    status = await get_chat_member_status(bot, chat_id, user_id)
    return status in ("administrator", "creator")
