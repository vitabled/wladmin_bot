"""Scheduled-posting commands (admins only, group-only) — Phase 5.

``/schedule <delay> [<interval>] | <text>`` — post once after ``delay``; if an
``interval`` is given, keep reposting every ``interval`` afterwards.
``/schedules`` lists them, ``/unschedule <id>`` removes one.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from sqlalchemy.ext.asyncio import AsyncSession

from bot.constants import (
    SCHEDULE_MAX_PER_CHAT,
    SCHEDULE_MIN_INTERVAL,
    SCHEDULE_PREVIEW_LEN,
)
from bot.db import crud
from bot.filters.chat_type import IsGroup
from bot.services.moderation import ModerationService
from bot.utils.text import escape_html, format_duration

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(IsGroup())


def _require_admin(data: dict[str, Any]) -> bool:
    return bool(data.get("is_admin"))


@router.message(Command("schedule"))
async def cmd_schedule(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return

    raw = command.args or ""
    if "|" not in raw:
        await message.reply(_("schedule_usage"))
        return
    timing, body = raw.split("|", 1)
    body = body.strip()
    parts = timing.split()
    if not body or not parts:
        await message.reply(_("schedule_usage"))
        return

    delay = ModerationService.parse_duration(parts[0])
    if delay is None:
        await message.reply(_("schedule_usage"))
        return

    interval: int | None = None
    if len(parts) > 1:
        interval = ModerationService.parse_duration(parts[1])
        if interval is None:
            await message.reply(_("schedule_usage"))
            return
        if interval < SCHEDULE_MIN_INTERVAL:
            await message.reply(
                _("schedule_min_interval", min=format_duration(SCHEDULE_MIN_INTERVAL))
            )
            return

    session: AsyncSession = data["session"]
    if (
        await crud.count_scheduled_posts(session, message.chat.id)
        >= SCHEDULE_MAX_PER_CHAT
    ):
        await message.reply(_("schedule_limit", max=SCHEDULE_MAX_PER_CHAT))
        return

    run_at = datetime.now(UTC) + timedelta(seconds=delay)
    actor_id = message.from_user.id if message.from_user else 0
    post = await crud.add_scheduled_post(
        session, message.chat.id, body, run_at, interval, actor_id
    )
    every = format_duration(interval) if interval else _("schedule_once")
    await message.reply(
        _("schedule_added", id=post.id, delay=format_duration(delay), every=every)
    )


@router.message(Command("schedules"))
async def cmd_schedules(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return
    session: AsyncSession = data["session"]
    posts = await crud.list_scheduled_posts(session, message.chat.id)
    if not posts:
        await message.reply(_("schedules_empty"))
        return
    lines = [
        _(
            "schedules_item",
            id=p.id,
            when=p.run_at.strftime("%Y-%m-%d %H:%M UTC"),
            every=(format_duration(p.interval_seconds) if p.interval_seconds else "—"),
            # Превью — пользовательский текст, экранируем (список шлётся в HTML).
            preview=escape_html(p.text[:SCHEDULE_PREVIEW_LEN]),
        )
        for p in posts
    ]
    await message.reply(_("schedules_list", count=len(posts), items="\n".join(lines)))


@router.message(Command("unschedule"))
async def cmd_unschedule(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return
    raw = (command.args or "").strip()
    if not raw.isdigit():
        await message.reply(_("schedule_usage"))
        return
    session: AsyncSession = data["session"]
    removed = await crud.remove_scheduled_post(session, message.chat.id, int(raw))
    await message.reply(_("unschedule_ok" if removed else "unschedule_not_found"))
