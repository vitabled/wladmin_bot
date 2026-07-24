"""User statistics / activity reports (Phase 4).

``record_activity`` is called from the per-message handler to count a real
user's messages; ``/stats`` and ``/top`` are commands (own router). Counting is
skipped for bots, anonymous/channel senders, and chats that opted out
(``stats_enabled``).
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from sqlalchemy.ext.asyncio import AsyncSession

from bot.constants import TOP_DEFAULT, TOP_MAX
from bot.db import crud
from bot.filters.chat_type import IsGroup
from bot.services.stats import StatsService
from bot.utils.text import build_mention

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(IsGroup())


async def record_activity(message: types.Message, data: dict[str, Any]) -> None:
    """Count a real user's message toward chat activity (best-effort)."""
    settings = data.get("settings")
    if not settings or not settings.get("stats_enabled", True):
        return
    user = message.from_user
    if user is None or user.is_bot or message.sender_chat is not None:
        return
    session: AsyncSession = data["session"]
    await crud.bump_activity(session, message.chat.id, user.id)


_TRUE = {"on", "true", "1", "yes", "вкл", "да"}
_FALSE = {"off", "false", "0", "no", "выкл", "нет"}


@router.message(Command("stats"))
async def cmd_stats(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    """Show the caller's stats, or `/stats on|off` toggles tracking (admins)."""
    _ = data["_"]
    session: AsyncSession = data["session"]

    arg = (command.args or "").strip().lower()
    if arg in _TRUE or arg in _FALSE:
        if not data.get("is_admin"):
            await message.reply(_("error_not_admin"))
            return
        val = arg in _TRUE
        await crud.update_settings(session, message.chat.id, stats_enabled=val)
        await data["redis"].invalidate_settings(message.chat.id)
        key = "ok_enabled" if val else "ok_disabled"
        await message.reply(_(key, feature=_("feature_stats")))
        return

    user = message.from_user
    mine = await crud.get_activity(session, message.chat.id, user.id) if user else 0
    total, users = await crud.chat_activity_totals(session, message.chat.id)
    share = StatsService.percentage(mine, total)
    await message.reply(
        _("stats_text", mine=mine, total=total, users=users, share=share)
    )


@router.message(Command("top"))
async def cmd_top(message: types.Message, command: CommandObject, **data: Any) -> None:
    """Show the most active users (`/top` or `/top N`)."""
    _ = data["_"]
    session: AsyncSession = data["session"]
    raw = (command.args or "").strip()
    requested = int(raw) if raw.lstrip("-").isdigit() else None
    limit = StatsService.clamp_top(requested, TOP_DEFAULT, TOP_MAX)

    rows = await crud.top_active(session, message.chat.id, limit)
    if not rows:
        await message.reply(_("top_empty"))
        return

    names = await crud.get_users_by_ids(session, [uid for uid, _c in rows])
    lines = [
        _(
            "top_item",
            medal=StatsService.medal(idx),
            user=build_mention(uid, names.get(uid) or str(uid)),
            count=count,
        )
        for idx, (uid, count) in enumerate(rows, start=1)
    ]
    body = _("top_header", count=len(rows)) + "\n" + "\n".join(lines)
    await message.reply(body, parse_mode="HTML")
