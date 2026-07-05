"""Reusable moderation actions shared by command handlers and antispam.

Каждая функция выполняет действие в Telegram (через safe-обёртки с ретраями)
и пишет запись в ``mod_log``. Отправку подтверждений в чат делает вызывающий
хендлер — так действия остаются переиспользуемыми.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from bot.constants import (
    ACTION_BAN,
    ACTION_KICK,
    ACTION_MUTE,
    MIN_RESTRICT_SECONDS,
)
from bot.db import crud
from bot.services.moderation import ModerationService
from bot.utils.telegram import (
    safe_ban_member,
    safe_kick_member,
    safe_mute_member,
    safe_unban_member,
    safe_unmute_member,
)


def _clamp(duration_seconds: int | None) -> int | None:
    """Telegram treats <30s restrictions as permanent; clamp temp ones up."""
    if duration_seconds is None:
        return None
    return max(duration_seconds, MIN_RESTRICT_SECONDS)


async def do_ban(
    bot: Bot,
    session: AsyncSession,
    chat_id: int,
    actor_id: int,
    target_id: int,
    duration_seconds: int | None,
    reason: str | None,
) -> bool:
    """Ban (temporary if duration given). Returns Telegram success.

    The audit log is written only when Telegram actually applied the action,
    so a swallowed 400/403 (bot lost rights, target gone) doesn't record a
    ban that never happened.
    """
    until = ModerationService.get_until_date_aware(_clamp(duration_seconds))
    ok = await safe_ban_member(bot, chat_id, target_id, until_date=until)
    if ok:
        await crud.add_mod_log(
            session,
            chat_id,
            actor_id,
            target_id,
            ACTION_BAN,
            reason,
            duration_seconds,
        )
    return ok


async def do_unban(
    bot: Bot,
    session: AsyncSession,
    chat_id: int,
    actor_id: int,
    target_id: int,
) -> bool:
    ok = await safe_unban_member(bot, chat_id, target_id, only_if_banned=True)
    if ok:
        await crud.add_mod_log(session, chat_id, actor_id, target_id, "unban")
    return ok


async def do_kick(
    bot: Bot,
    session: AsyncSession,
    chat_id: int,
    actor_id: int,
    target_id: int,
    reason: str | None = None,
) -> bool:
    ok = await safe_kick_member(bot, chat_id, target_id)
    if ok:
        await crud.add_mod_log(
            session, chat_id, actor_id, target_id, ACTION_KICK, reason
        )
    return ok


async def do_mute(
    bot: Bot,
    session: AsyncSession,
    chat_id: int,
    actor_id: int,
    target_id: int,
    duration_seconds: int | None,
    reason: str | None,
) -> bool:
    until = ModerationService.get_until_date_aware(_clamp(duration_seconds))
    ok = await safe_mute_member(bot, chat_id, target_id, until_date=until)
    if ok:
        await crud.add_mod_log(
            session,
            chat_id,
            actor_id,
            target_id,
            ACTION_MUTE,
            reason,
            duration_seconds,
        )
    return ok


async def do_unmute(
    bot: Bot,
    session: AsyncSession,
    chat_id: int,
    actor_id: int,
    target_id: int,
) -> bool:
    ok = await safe_unmute_member(bot, chat_id, target_id)
    if ok:
        await crud.add_mod_log(session, chat_id, actor_id, target_id, "unmute")
    return ok


@dataclass
class WarnOutcome:
    """Result of issuing a warn."""

    count: int
    limit: int
    action_applied: str | None = None


async def do_warn(
    bot: Bot,
    session: AsyncSession,
    chat_id: int,
    actor_id: int,
    target_id: int,
    reason: str | None,
    settings: dict[str, Any],
) -> WarnOutcome:
    """Issue a warn; auto-apply ``warn_action`` when the limit is reached.

    On auto-punishment active warns are reset so the action doesn't re-fire on
    every subsequent warn.
    """
    count = await crud.add_warn(session, chat_id, target_id, actor_id, reason)
    limit = int(settings["warn_limit"])
    await crud.add_mod_log(session, chat_id, actor_id, target_id, "warn", reason)

    if count < limit:
        return WarnOutcome(count=count, limit=limit, action_applied=None)

    action = settings["warn_action"]
    duration = settings.get("warn_action_duration")
    if action == ACTION_BAN:
        await do_ban(bot, session, chat_id, actor_id, target_id, duration, None)
    elif action == ACTION_KICK:
        await do_kick(bot, session, chat_id, actor_id, target_id)
    else:  # default mute
        await do_mute(bot, session, chat_id, actor_id, target_id, duration, None)

    await crud.deactivate_all_warns(session, chat_id, target_id)
    return WarnOutcome(count=count, limit=limit, action_applied=action)
