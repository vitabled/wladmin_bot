"""Seller-reputation commands (Phase 9).

``/scam`` (anyone, groups and PM) — check a seller against the global scam
list and, when the user is unknown to the lists, assess join-age risk:

* in ``scam_list`` with source=scam      → «в списке скама» verdict;
* in ``scam_list`` with source=verified  → «проверенный продавец»;
* otherwise — a group member who joined less than ``SCAM_JOINED_RISK_DAYS``
  ago is a risk factor (account age is NOT available via the Bot API, so that
  line is honestly omitted rather than guessed);
* every answer carries the report-this-scammer footer.

``/addtowl`` (admins/owner only) — whitelist a seller (source=verified) or
``/addtowl remove <target>`` to drop the record.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import Bot, Router, types
from aiogram.filters import Command, CommandObject
from sqlalchemy.ext.asyncio import AsyncSession

from bot.cache.redis import RedisClient
from bot.constants import (
    SCAM_AUTO_WARN_DAYS,
    SCAM_JOINED_RISK_DAYS,
    SCAM_SOURCE_SCAM,
    SCAM_SOURCE_VERIFIED,
)
from bot.db import crud
from bot.utils.targets import resolve_target
from bot.utils.text import build_mention, escape_html

logger = logging.getLogger(__name__)

router = Router()

_GROUP_TYPES = ("group", "supergroup")


async def _risk_factors(
    bot: Bot, chat: types.Chat | None, user_id: int
) -> list[str]:
    """Collect measurable risk signals (i18n keys), honest about gaps.

    Bot API gives us no joined_date (aiogram 3.29 ChatMember has no such
    field), so the only factual signal is the join date from MTProto
    ``channels.getParticipant`` (bot token). Anything we cannot verify is
    omitted — never guessed.
    """
    factors: list[str] = []
    if chat is None or chat.type not in _GROUP_TYPES:
        return factors
    try:
        from bot.utils import join_date

        joined = await join_date.get_joined_date(chat.id, user_id)
    except Exception:
        joined = None
    if joined is None:
        return factors
    try:
        if joined.tzinfo is None:
            joined = joined.replace(tzinfo=UTC)
        if datetime.now(UTC) - joined < timedelta(days=SCAM_JOINED_RISK_DAYS):
            factors.append("scam_risk_joined")
    except TypeError:
        pass
    return factors


@router.message(Command("scam"))
async def cmd_scam(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    """Check a seller: scam list → verified list → risk assessment."""
    _ = data["_"]
    session: AsyncSession = data["session"]
    bot: Bot = message.bot

    target, error_key, _consumed = await resolve_target(
        message, (command.args or "").split(), session, bot
    )
    if error_key is not None or target is None:
        # Для /scam общий "error_no_target" заменяем на дружелюбный вариант
        # про продавца; остальные ошибки (канал/не найдено) — как в модерации.
        key = (
            "scam_no_target"
            if error_key == "error_no_target"
            else (error_key or "scam_no_target")
        )
        await message.reply(_(key))
        return

    # Если целью оказался сам бот (типичный случай "/scam @lotesadminbot" —
    # клиент подставляет username бота как аргумент), это не цель, а её
    # отсутствие: показываем подсказку, а не «проверяем» бота.
    if target.user_id == bot.id:
        await message.reply(_("scam_no_target"))
        return

    entry = await crud.get_scam_entry(session, target.user_id)
    mention = build_mention(target.user_id, target.name)

    if entry is not None and entry.source == SCAM_SOURCE_SCAM:
        reason = escape_html(entry.reason) if entry.reason else _("no_reason")
        body = _("scam_found", user=mention, reason=reason)
    elif entry is not None and entry.source == SCAM_SOURCE_VERIFIED:
        body = _("scam_verified", user=mention)
    else:
        factors = await _risk_factors(bot, message.chat, target.user_id)
        if factors:
            # Возраст аккаунта: Telegram не отдаёт дату создания (ни Bot API,
            # ни MTProto) — провайдер честно вернёт None, пока нет источника.
            from bot.utils import account_age

            age_days = await account_age.get_account_age_days(target.user_id)
            if age_days is not None and age_days > account_age.ACCOUNT_AGE_RISK_DAYS:
                factors.append("scam_risk_age")
            listed = "\n".join(_(key) for key in factors)
            body = _("scam_risk", factors=listed)
        else:
            body = _("scam_ok")

    await message.reply(f"{body}\n\n{_('scam_footer')}", parse_mode="HTML")


async def maybe_warn_newbie(message: types.Message, data: dict[str, Any]) -> bool:
    """Авто-предупреждение о высоком риске для участника младше суток.

    Вызывается из per-message хендлера после антиспама: если автор —
    реальный пользователь (не админ/бот/канал), вступивший в чат меньше
    ``SCAM_AUTO_WARN_DAYS`` дней назад, бот один раз (флаг в Redis на период)
    отвечает предупреждением о высоком риске скама.
    """
    settings = data.get("settings")
    if not settings:
        return False
    if data.get("is_admin"):
        return False
    user = message.from_user
    if user is None or user.is_bot:
        return False
    if message.sender_chat is not None or message.is_automatic_forward:
        return False
    try:
        from bot.utils import join_date

        joined = await join_date.get_joined_date(message.chat.id, user.id)
    except Exception:
        joined = None
    if joined is None:
        return False
    try:
        if joined.tzinfo is None:
            joined = joined.replace(tzinfo=UTC)
        if datetime.now(UTC) - joined >= timedelta(days=SCAM_AUTO_WARN_DAYS):
            return False
    except TypeError:
        return False

    redis: RedisClient = data["redis"]
    flag_key = f"scam_auto_warn:{message.chat.id}:{user.id}"
    if await redis.get(flag_key):
        return False
    await redis.set(flag_key, "1", ttl=int(SCAM_AUTO_WARN_DAYS * 86400))

    _ = data["_"]
    mention = build_mention(user.id, user.first_name or "")
    await message.reply(
        f"{_('scam_auto_warn', user=mention)}\n\n{_('scam_footer')}",
        parse_mode="HTML",
    )
    return True


@router.message(Command("addtowl"))
async def cmd_addtowl(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    """Whitelist a seller (source=verified); ``remove`` drops the record."""
    _ = data["_"]
    if not data.get("is_admin"):
        await message.reply(_("error_not_admin"))
        return

    session: AsyncSession = data["session"]
    bot: Bot = message.bot
    args = (command.args or "").split()
    remove_mode = bool(args) and args[0].lower() == "remove"
    if remove_mode:
        args = args[1:]

    target, error_key, _consumed = await resolve_target(message, args, session, bot)
    if error_key is not None or target is None:
        await message.reply(_(error_key or "addtowl_usage"))
        return

    # Не даём заносить в белый список самого бота (тот же кейс, что и в /scam:
    # "/addtowl @lotesadminbot" резолвится в собственный username бота).
    if target.user_id == bot.id:
        await message.reply(_("error_cannot_act_on_bot"))
        return

    mention = build_mention(target.user_id, target.name)
    if remove_mode:
        removed = await crud.remove_scam_entry(session, target.user_id)
        key = "addtowl_removed" if removed else "addtowl_not_found"
    else:
        # Upsert: whitelisting a previously flagged user overrides source.
        await crud.upsert_scam_entry(
            session, target.user_id, SCAM_SOURCE_VERIFIED, None
        )
        key = "addtowl_added"
    await message.reply(_(key, user=mention), parse_mode="HTML")
