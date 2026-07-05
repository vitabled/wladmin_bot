"""Moderation command handlers: /ban /unban /kick /mute /unmute /warn ..."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aiogram import Bot, Router, types
from aiogram.filters import Command, CommandObject
from sqlalchemy.ext.asyncio import AsyncSession

from bot.filters.chat_type import IsGroup
from bot.handlers import actions
from bot.services.moderation import ModerationService
from bot.utils.targets import Target, resolve_target
from bot.utils.telegram import bot_can_restrict, is_user_admin
from bot.utils.text import build_mention, escape_html, format_duration

router = Router()
router.message.filter(IsGroup())


@dataclass
class Prepared:
    """Resolved command context after guards passed."""

    target: Target
    duration: int | None
    reason: str | None


def _reason_suffix(_: Callable[..., str], reason: str | None) -> str:
    # Reason is admin free-text; escape it since replies use HTML parse mode.
    return _("reason_suffix", reason=escape_html(reason)) if reason else ""


async def _prepare(
    message: types.Message,
    command: CommandObject,
    data: dict[str, Any],
    *,
    allow_duration: bool,
    protect_target: bool,
    need_restrict: bool,
) -> Prepared | None:
    """Run shared guards and resolve target/duration/reason.

    Returns ``None`` (after replying with a localized error) if any guard
    fails: not-admin actor, bot lacks rights, no/invalid target, or the target
    is protected (self / bot / owner / admin).
    """
    _ = data["_"]
    bot: Bot = message.bot
    chat = message.chat
    actor = message.from_user
    session: AsyncSession = data["session"]

    if not data.get("is_admin"):
        await message.reply(_("error_not_admin"))
        return None

    # Fresh re-check of the ACTOR's rights: the cached is_admin flag may be up
    # to its TTL stale, so a just-demoted admin could otherwise still act.
    # Skip anonymous admins (posting as the chat) and the bot owner.
    if (
        message.sender_chat is None
        and not data.get("is_owner")
        and actor is not None
        and not await is_user_admin(bot, chat.id, actor.id)
    ):
        await message.reply(_("error_not_admin"))
        return None

    if need_restrict and not await bot_can_restrict(bot, chat.id, bot.id):
        await message.reply(_("error_bot_cant_restrict"))
        return None

    args = (command.args or "").split()
    target, err, consumed = await resolve_target(message, args, session, bot)
    if err is not None or target is None:
        await message.reply(_(err or "error_no_target"))
        return None

    if target.user_id == bot.id:
        await message.reply(_("error_cannot_act_on_bot"))
        return None
    if actor is not None and target.user_id == actor.id:
        await message.reply(_("error_cannot_act_on_self"))
        return None

    if protect_target:
        if target.user_id == data.get("owner_id"):
            await message.reply(_("error_cannot_act_on_owner"))
            return None
        if await is_user_admin(bot, chat.id, target.user_id):
            await message.reply(_("error_cannot_act_on_admin"))
            return None

    rest = args[consumed:]
    duration: int | None = None
    reason_tokens = rest
    if allow_duration and rest:
        parsed = ModerationService.parse_duration(rest[0])
        if parsed is not None:
            duration = parsed
            reason_tokens = rest[1:]
    reason = " ".join(reason_tokens).strip() or None
    return Prepared(target=target, duration=duration, reason=reason)


@router.message(Command("ban"))
async def cmd_ban(message: types.Message, command: CommandObject, **data: Any) -> None:
    prep = await _prepare(
        message,
        command,
        data,
        allow_duration=True,
        protect_target=True,
        need_restrict=True,
    )
    if prep is None:
        return
    _ = data["_"]
    ok = await actions.do_ban(
        message.bot,
        data["session"],
        message.chat.id,
        message.from_user.id,
        prep.target.user_id,
        prep.duration,
        prep.reason,
    )
    if not ok:
        await message.reply(_("error_bot_not_admin"))
        return
    mention = build_mention(prep.target.user_id, prep.target.name)
    suffix = _reason_suffix(_, prep.reason)
    if prep.duration:
        text = _(
            "mod_ban_temp",
            user=mention,
            duration=format_duration(prep.duration),
            reason=suffix,
        )
    else:
        text = _("mod_ban", user=mention, reason=suffix)
    await message.answer(text, parse_mode="HTML")


@router.message(Command("unban"))
async def cmd_unban(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    prep = await _prepare(
        message,
        command,
        data,
        allow_duration=False,
        protect_target=False,
        need_restrict=True,
    )
    if prep is None:
        return
    _ = data["_"]
    ok = await actions.do_unban(
        message.bot,
        data["session"],
        message.chat.id,
        message.from_user.id,
        prep.target.user_id,
    )
    mention = build_mention(prep.target.user_id, prep.target.name)
    key = "mod_unban" if ok else "mod_not_banned"
    await message.answer(_(key, user=mention), parse_mode="HTML")


@router.message(Command("kick"))
async def cmd_kick(message: types.Message, command: CommandObject, **data: Any) -> None:
    prep = await _prepare(
        message,
        command,
        data,
        allow_duration=False,
        protect_target=True,
        need_restrict=True,
    )
    if prep is None:
        return
    _ = data["_"]
    ok = await actions.do_kick(
        message.bot,
        data["session"],
        message.chat.id,
        message.from_user.id,
        prep.target.user_id,
        prep.reason,
    )
    if not ok:
        await message.reply(_("error_bot_not_admin"))
        return
    mention = build_mention(prep.target.user_id, prep.target.name)
    await message.answer(
        _("mod_kick", user=mention, reason=_reason_suffix(_, prep.reason)),
        parse_mode="HTML",
    )


@router.message(Command("mute"))
async def cmd_mute(message: types.Message, command: CommandObject, **data: Any) -> None:
    prep = await _prepare(
        message,
        command,
        data,
        allow_duration=True,
        protect_target=True,
        need_restrict=True,
    )
    if prep is None:
        return
    _ = data["_"]
    ok = await actions.do_mute(
        message.bot,
        data["session"],
        message.chat.id,
        message.from_user.id,
        prep.target.user_id,
        prep.duration,
        prep.reason,
    )
    if not ok:
        await message.reply(_("error_bot_not_admin"))
        return
    mention = build_mention(prep.target.user_id, prep.target.name)
    suffix = _reason_suffix(_, prep.reason)
    if prep.duration:
        text = _(
            "mod_mute_temp",
            user=mention,
            duration=format_duration(prep.duration),
            reason=suffix,
        )
    else:
        text = _("mod_mute", user=mention, reason=suffix)
    await message.answer(text, parse_mode="HTML")


@router.message(Command("unmute"))
async def cmd_unmute(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    prep = await _prepare(
        message,
        command,
        data,
        allow_duration=False,
        protect_target=False,
        need_restrict=True,
    )
    if prep is None:
        return
    _ = data["_"]
    await actions.do_unmute(
        message.bot,
        data["session"],
        message.chat.id,
        message.from_user.id,
        prep.target.user_id,
    )
    mention = build_mention(prep.target.user_id, prep.target.name)
    await message.answer(_("mod_unmute", user=mention), parse_mode="HTML")


@router.message(Command("warn"))
async def cmd_warn(message: types.Message, command: CommandObject, **data: Any) -> None:
    prep = await _prepare(
        message,
        command,
        data,
        allow_duration=False,
        protect_target=True,
        need_restrict=True,
    )
    if prep is None:
        return
    _ = data["_"]
    outcome = await actions.do_warn(
        message.bot,
        data["session"],
        message.chat.id,
        message.from_user.id,
        prep.target.user_id,
        prep.reason,
        data["settings"],
    )
    mention = build_mention(prep.target.user_id, prep.target.name)
    if outcome.action_applied:
        text = _(
            "mod_warn_action",
            user=mention,
            count=outcome.count,
            limit=outcome.limit,
            action=outcome.action_applied,
        )
    else:
        text = _(
            "mod_warn",
            user=mention,
            count=outcome.count,
            limit=outcome.limit,
            reason=_reason_suffix(_, prep.reason),
        )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("unwarn"))
async def cmd_unwarn(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    prep = await _prepare(
        message,
        command,
        data,
        allow_duration=False,
        protect_target=False,
        need_restrict=False,
    )
    if prep is None:
        return
    _ = data["_"]
    from bot.db import crud

    session = data["session"]
    mention = build_mention(prep.target.user_id, prep.target.name)
    removed = await crud.deactivate_last_warn(
        session, message.chat.id, prep.target.user_id
    )
    if not removed:
        await message.answer(_("mod_unwarn_none", user=mention), parse_mode="HTML")
        return
    count = await crud.count_active_warns(session, message.chat.id, prep.target.user_id)
    await crud.add_mod_log(
        session,
        message.chat.id,
        message.from_user.id,
        prep.target.user_id,
        "unwarn",
    )
    await message.answer(_("mod_unwarn", user=mention, count=count), parse_mode="HTML")


@router.message(Command("warns"))
async def cmd_warns(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    prep = await _prepare(
        message,
        command,
        data,
        allow_duration=False,
        protect_target=False,
        need_restrict=False,
    )
    if prep is None:
        return
    _ = data["_"]
    from bot.db import crud

    session = data["session"]
    mention = build_mention(prep.target.user_id, prep.target.name)
    warns = await crud.list_active_warns(session, message.chat.id, prep.target.user_id)
    limit = int(data["settings"]["warn_limit"])
    if not warns:
        await message.answer(_("mod_warns_none", user=mention), parse_mode="HTML")
        return
    lines = [_("mod_warns_header", user=mention, count=len(warns), limit=limit)]
    for idx, warn in enumerate(warns, start=1):
        lines.append(
            _(
                "mod_warns_item",
                index=idx,
                reason=escape_html(warn.reason) if warn.reason else _("no_reason"),
                date=warn.created_at.strftime("%Y-%m-%d %H:%M"),
            )
        )
    await message.answer("\n".join(lines), parse_mode="HTML")
