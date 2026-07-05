"""Resolve a moderation target from a command (reply / mention / id / @username)."""

from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud


@dataclass
class Target:
    """A resolved moderation target."""

    user_id: int
    name: str
    username: str | None = None


async def resolve_target(
    message: Message,
    args: list[str],
    session: AsyncSession,
    bot: Bot,
) -> tuple[Target | None, str | None, int]:
    """Resolve who a moderation command targets.

    Order: reply → text_mention entity → numeric id → @username (DB, then
    Bot API). Returns ``(target, error_key, consumed)`` where ``consumed`` is
    how many leading tokens of ``args`` the target occupied (0 for a reply) so
    the caller can parse duration/reason from ``args[consumed:]``.
    ``error_key`` is an i18n key on failure (target is ``None`` then).
    """
    reply = message.reply_to_message
    if reply is not None:
        # Anonymous admins / channel auto-forwards carry a sender_chat, not a
        # real user — refuse to "moderate a channel".
        if reply.sender_chat is not None:
            return None, "error_cannot_act_on_channel", 0
        if reply.from_user is not None:
            u = reply.from_user
            return Target(u.id, u.full_name, u.username), None, 0

    # text_mention entities embed the real user object (works without username).
    for entity in message.entities or []:
        if entity.type == "text_mention" and entity.user is not None:
            u = entity.user
            return Target(u.id, u.full_name, u.username), None, 1

    if not args:
        return None, "error_no_target", 0

    token = args[0]

    # Numeric user id (always a positive integer; reject "--5" and the like
    # so int() can't raise on a malformed token).
    if token.isdigit():
        return Target(int(token), token, None), None, 1

    # @username → cached user, else ask Telegram.
    if token.startswith("@") and len(token) > 1:
        cached = await crud.get_user_by_username(session, token)
        if cached is not None:
            return (
                Target(cached.user_id, cached.first_name, cached.username),
                None,
                1,
            )
        try:
            chat = await bot.get_chat(token)
        except Exception:
            return None, "error_target_not_found", 1
        if chat.type != "private":
            return None, "error_cannot_act_on_channel", 1
        name = chat.full_name or (chat.username or token)
        return Target(chat.id, name, chat.username), None, 1

    return None, "error_no_target", 0
