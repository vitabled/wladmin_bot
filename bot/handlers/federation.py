"""Federated groups: shared ban lists across chats (Phase 8).

Команды (только для админов чата):
  /fcreate <name>   — создать федерацию (создатель = владелец)
  /fjoin <fed_id>   — привязать текущий чат (только владелец федерации)
  /fleave           — отвязать текущий чат
  /fban <target>    — забанить пользователя во всех чатах федерации
  /funban <target>  — снять фед-бан во всех чатах
  /finfo            — сведения о федерации текущего чата
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot, Router, types
from aiogram.filters import Command, CommandObject
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.filters.chat_type import IsGroup
from bot.utils.text import escape_html
from bot.services.federation import FederationService
from bot.utils.targets import resolve_target
from bot.utils.telegram import safe_ban_member, safe_unban_member

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(IsGroup())


def _require_admin(data: dict[str, Any]) -> bool:
    return bool(data.get("is_admin"))


@router.message(Command("fcreate"))
async def cmd_fcreate(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return
    name = FederationService.normalize_name(command.args or "")
    if not FederationService.is_valid_name(name):
        await message.reply(_("fed_name_invalid"))
        return
    session: AsyncSession = data["session"]
    actor = message.from_user.id if message.from_user else 0
    fed = await crud.create_federation(session, name, actor)
    if fed is None:
        await message.reply(_("fed_name_taken"))
        return
    await message.reply(_("fed_created", name=name, id=fed.id))


@router.message(Command("fjoin"))
async def cmd_fjoin(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return
    raw = (command.args or "").strip()
    if not raw.isdigit():
        await message.reply(_("fed_join_usage"))
        return
    session: AsyncSession = data["session"]
    fed = await crud.get_federation(session, int(raw))
    if fed is None:
        await message.reply(_("fed_not_found"))
        return
    actor = message.from_user.id if message.from_user else 0
    if actor != fed.owner_id and not data.get("is_owner"):
        await message.reply(_("fed_join_forbidden"))
        return
    added = await crud.add_chat_to_federation(session, fed.id, message.chat.id)
    if not added:
        await message.reply(_("fed_already_joined"))
        return
    await message.reply(_("fed_joined", name=escape_html(fed.name)))


@router.message(Command("fleave"))
async def cmd_fleave(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return
    session: AsyncSession = data["session"]
    removed = await crud.remove_chat_from_federation(session, message.chat.id)
    await message.reply(_("fed_left" if removed else "fed_not_in"))


@router.message(Command("finfo"))
async def cmd_finfo(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    _ = data["_"]
    session: AsyncSession = data["session"]
    fed = await crud.get_chat_federation(session, message.chat.id)
    if fed is None:
        await message.reply(_("fed_not_in"))
        return
    chats = await crud.count_federation_chats(session, fed.id)
    bans = await crud.count_fedbans(session, fed.id)
    await message.reply(_("fed_info", name=escape_html(fed.name), id=fed.id, chats=chats, bans=bans))


async def _resolve_for_fed(
    message: types.Message, command: CommandObject, data: dict[str, Any]
) -> tuple[Any, Any, list[str], int] | None:
    """Shared setup for /fban and /funban: federation + resolved target."""
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return None
    session: AsyncSession = data["session"]
    fed = await crud.get_chat_federation(session, message.chat.id)
    if fed is None:
        await message.reply(_("fed_not_in"))
        return None
    bot: Bot = message.bot
    args = (command.args or "").split()
    target, error_key, consumed = await resolve_target(message, args, session, bot)
    if error_key is not None or target is None:
        await message.reply(_(error_key or "error_no_target"))
        return None
    return fed, target, args, consumed


@router.message(Command("fban"))
async def cmd_fban(message: types.Message, command: CommandObject, **data: Any) -> None:
    _ = data["_"]
    resolved = await _resolve_for_fed(message, command, data)
    if resolved is None:
        return
    fed, target, args, consumed = resolved

    actor = message.from_user.id if message.from_user else 0
    if target.user_id in (fed.owner_id, data.get("owner_id"), actor):
        await message.reply(_("fed_cannot_ban"))
        return

    session: AsyncSession = data["session"]
    bot: Bot = message.bot
    reason = " ".join(args[consumed:]).strip() or None
    added = await crud.add_fedban(session, fed.id, target.user_id, reason, actor)
    if not added:
        await message.reply(_("fed_already_banned", user=escape_html(target.name)))
        return

    await crud.add_mod_log(
        session, message.chat.id, actor, target.user_id, "fedban", reason
    )
    chat_ids = await crud.list_federation_chats(session, fed.id)
    for chat_id in chat_ids:
        await safe_ban_member(bot, chat_id, target.user_id)
    await message.reply(
        _("fed_banned", user=escape_html(target.name), name=escape_html(fed.name), count=len(chat_ids))
    )


@router.message(Command("funban"))
async def cmd_funban(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    _ = data["_"]
    resolved = await _resolve_for_fed(message, command, data)
    if resolved is None:
        return
    fed, target, _args, _consumed = resolved

    session: AsyncSession = data["session"]
    bot: Bot = message.bot
    removed = await crud.remove_fedban(session, fed.id, target.user_id)
    if not removed:
        await message.reply(_("fed_not_banned", user=escape_html(target.name)))
        return

    actor = message.from_user.id if message.from_user else 0
    await crud.add_mod_log(
        session, message.chat.id, actor, target.user_id, "fedunban", None
    )
    chat_ids = await crud.list_federation_chats(session, fed.id)
    for chat_id in chat_ids:
        await safe_unban_member(bot, chat_id, target.user_id, only_if_banned=True)
    await message.reply(_("fed_unbanned", user=escape_html(target.name), name=escape_html(fed.name)))
