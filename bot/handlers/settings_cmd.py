"""Chat settings commands (admins only, group-only)."""

from __future__ import annotations

from typing import Any

from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from sqlalchemy.ext.asyncio import AsyncSession

from bot.cache.redis import RedisClient
from bot.constants import (
    ANTIFLOOD_ACTIONS,
    ANTIFLOOD_LIMIT_MAX,
    ANTIFLOOD_LIMIT_MIN,
    ANTIFLOOD_WINDOW_MAX,
    ANTIFLOOD_WINDOW_MIN,
    CAPTCHA_TYPES,
    NEWBIE_PERIOD_MAX,
    NEWBIE_PERIOD_MIN,
    TRIGGER_MAX_PER_CHAT,
    TRIGGER_SEPARATOR,
    WARN_ACTIONS,
    WARN_LIMIT_MAX,
    WARN_LIMIT_MIN,
)
from bot.db import crud
from bot.filters.chat_type import IsGroup
from bot.services.moderation import ModerationService
from bot.utils.text import format_duration

router = Router()
router.message.filter(IsGroup())

_TRUE = {"on", "true", "1", "yes", "вкл", "да"}
_FALSE = {"off", "false", "0", "no", "выкл", "нет"}

_ANTISPAM_FIELDS = {
    "links": "filter_links",
    "forwards": "filter_forwards",
    "stopwords": "filter_stopwords",
}


def _parse_onoff(value: str) -> bool | None:
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return None


def _require_admin(data: dict[str, Any]) -> bool:
    return bool(data.get("is_admin"))


async def _save(data: dict[str, Any], chat_id: int, **fields: Any) -> None:
    """Persist settings and invalidate the Redis cache."""
    session: AsyncSession = data["session"]
    redis: RedisClient = data["redis"]
    await crud.update_settings(session, chat_id, **fields)
    await redis.invalidate_settings(chat_id)


@router.message(Command("settings"))
async def cmd_settings(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return
    s = data["settings"]

    def onoff(flag: Any) -> str:
        return _("value_on") if flag else _("value_off")

    text = _(
        "settings_text",
        welcome_enabled=onoff(s["welcome_enabled"]),
        captcha_enabled=onoff(s["captcha_enabled"]),
        captcha_type=s["captcha_type"],
        captcha_timeout=s["captcha_timeout"],
        captcha_fail_action=s["captcha_fail_action"],
        warn_limit=s["warn_limit"],
        warn_action=s["warn_action"],
        filter_links=onoff(s["filter_links"]),
        filter_forwards=onoff(s["filter_forwards"]),
        filter_stopwords=onoff(s["filter_stopwords"]),
        antispam_action=s["antispam_action"],
        antiflood_enabled=onoff(s.get("antiflood_enabled")),
        antiflood_limit=s.get("antiflood_limit", 5),
        antiflood_window=s.get("antiflood_window", 5),
        antiflood_action=s.get("antiflood_action", "mute"),
        newbie_media=onoff(s.get("newbie_media_enabled")),
        newbie_period=s.get("newbie_period", 3600),
        triggers_enabled=onoff(s.get("triggers_enabled")),
        stats_enabled=onoff(s.get("stats_enabled", True)),
    )
    await message.reply(text)


async def _toggle_feature(
    message: types.Message,
    command: CommandObject,
    data: dict[str, Any],
    *,
    field: str,
    feature_key: str,
    cmd: str,
) -> None:
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return
    val = _parse_onoff(command.args or "")
    if val is None:
        await message.reply(_("usage_on_off", cmd=cmd))
        return
    await _save(data, message.chat.id, **{field: val})
    key = "ok_enabled" if val else "ok_disabled"
    await message.reply(_(key, feature=_(feature_key)))


@router.message(Command("welcome"))
async def cmd_welcome(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    await _toggle_feature(
        message,
        command,
        data,
        field="welcome_enabled",
        feature_key="feature_welcome",
        cmd="/welcome",
    )


@router.message(Command("captcha"))
async def cmd_captcha(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    await _toggle_feature(
        message,
        command,
        data,
        field="captcha_enabled",
        feature_key="feature_captcha",
        cmd="/captcha",
    )


@router.message(Command("setwelcome"))
async def cmd_setwelcome(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return
    text = (command.args or "").strip()
    if not text:
        await message.reply(_("welcome_empty"))
        return
    await _save(data, message.chat.id, welcome_text=text, welcome_enabled=True)
    await message.reply(_("welcome_set"))


@router.message(Command("setcaptcha"))
async def cmd_setcaptcha(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return
    ctype = (command.args or "").strip().lower()
    if ctype not in CAPTCHA_TYPES:
        await message.reply(_("captcha_type_invalid"))
        return
    await _save(data, message.chat.id, captcha_type=ctype)
    await message.reply(_("captcha_type_set", type=ctype))


@router.message(Command("setwarnlimit"))
async def cmd_setwarnlimit(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return
    raw = (command.args or "").strip()
    if not raw.lstrip("-").isdigit():
        await message.reply(
            _("warn_limit_invalid", min=WARN_LIMIT_MIN, max=WARN_LIMIT_MAX)
        )
        return
    limit = int(raw)
    if not WARN_LIMIT_MIN <= limit <= WARN_LIMIT_MAX:
        await message.reply(
            _("warn_limit_invalid", min=WARN_LIMIT_MIN, max=WARN_LIMIT_MAX)
        )
        return
    await _save(data, message.chat.id, warn_limit=limit)
    await message.reply(_("warn_limit_set", limit=limit))


@router.message(Command("setwarnaction"))
async def cmd_setwarnaction(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return
    parts = (command.args or "").split()
    if not parts or parts[0].lower() not in WARN_ACTIONS:
        await message.reply(_("warn_action_invalid"))
        return
    action = parts[0].lower()
    duration = ModerationService.parse_duration(parts[1]) if len(parts) > 1 else None
    await _save(
        data,
        message.chat.id,
        warn_action=action,
        warn_action_duration=duration,
    )
    suffix = f" ({format_duration(duration)})" if duration else ""
    await message.reply(_("warn_action_set", action=action, duration=suffix))


@router.message(Command("antispam"))
async def cmd_antispam(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return
    parts = (command.args or "").split()
    if len(parts) < 2 or parts[0].lower() not in _ANTISPAM_FIELDS:
        await message.reply(_("antispam_usage"))
        return
    field = _ANTISPAM_FIELDS[parts[0].lower()]
    val = _parse_onoff(parts[1])
    if val is None:
        await message.reply(_("antispam_usage"))
        return
    await _save(data, message.chat.id, **{field: val})
    state = _("value_on") if val else _("value_off")
    await message.reply(_("antispam_set", filter=parts[0].lower(), state=state))


@router.message(Command("antiflood"))
async def cmd_antiflood(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    """`/antiflood on|off` toggles; `/antiflood <limit> <window> [action]` configures."""
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return
    parts = (command.args or "").split()
    if not parts:
        await message.reply(_("antiflood_usage"))
        return

    onoff = _parse_onoff(parts[0])
    if onoff is not None and len(parts) == 1:
        await _save(data, message.chat.id, antiflood_enabled=onoff)
        key = "ok_enabled" if onoff else "ok_disabled"
        await message.reply(_(key, feature=_("feature_antiflood")))
        return

    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        await message.reply(_("antiflood_usage"))
        return
    limit, window = int(parts[0]), int(parts[1])
    if not (ANTIFLOOD_LIMIT_MIN <= limit <= ANTIFLOOD_LIMIT_MAX) or not (
        ANTIFLOOD_WINDOW_MIN <= window <= ANTIFLOOD_WINDOW_MAX
    ):
        await message.reply(_("antiflood_usage"))
        return
    action = parts[2].lower() if len(parts) > 2 else "mute"
    if action not in ANTIFLOOD_ACTIONS:
        await message.reply(_("antiflood_usage"))
        return
    await _save(
        data,
        message.chat.id,
        antiflood_enabled=True,
        antiflood_limit=limit,
        antiflood_window=window,
        antiflood_action=action,
    )
    await message.reply(_("antiflood_set", limit=limit, window=window, action=action))


@router.message(Command("newbie"))
async def cmd_newbie(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    """`/newbie on|off` toggles; `/newbie <seconds>` sets the probation window."""
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return
    parts = (command.args or "").split()
    if not parts:
        await message.reply(_("newbie_usage"))
        return

    onoff = _parse_onoff(parts[0])
    if onoff is not None:
        await _save(data, message.chat.id, newbie_media_enabled=onoff)
        key = "ok_enabled" if onoff else "ok_disabled"
        await message.reply(_(key, feature=_("feature_newbie")))
        return

    if not parts[0].isdigit():
        await message.reply(_("newbie_usage"))
        return
    period = int(parts[0])
    if not NEWBIE_PERIOD_MIN <= period <= NEWBIE_PERIOD_MAX:
        await message.reply(_("newbie_usage"))
        return
    await _save(
        data,
        message.chat.id,
        newbie_media_enabled=True,
        newbie_period=period,
    )
    await message.reply(_("newbie_set", period=period))


@router.message(Command("addstop"))
async def cmd_addstop(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return
    word = (command.args or "").strip()
    if not word:
        await message.reply(_("stopword_empty"))
        return
    session: AsyncSession = data["session"]
    redis: RedisClient = data["redis"]
    added = await crud.add_stopword(session, message.chat.id, word)
    await redis.invalidate_stopwords(message.chat.id)
    key = "stopword_added" if added else "stopword_exists"
    await message.reply(_(key, word=word.lower()))


@router.message(Command("delstop"))
async def cmd_delstop(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return
    word = (command.args or "").strip()
    if not word:
        await message.reply(_("stopword_empty"))
        return
    session: AsyncSession = data["session"]
    redis: RedisClient = data["redis"]
    removed = await crud.remove_stopword(session, message.chat.id, word)
    await redis.invalidate_stopwords(message.chat.id)
    key = "stopword_removed" if removed else "stopword_not_found"
    await message.reply(_(key, word=word.lower()))


@router.message(Command("stopwords"))
async def cmd_stopwords(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return
    session: AsyncSession = data["session"]
    words = await crud.list_stopwords(session, message.chat.id)
    if not words:
        await message.reply(_("stopwords_empty"))
        return
    listing = "\n".join(f"• {w}" for w in words)
    await message.reply(_("stopwords_list", count=len(words), words=listing))


@router.message(Command("addtrigger"))
async def cmd_addtrigger(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    """`/addtrigger <pattern> | <reply>` — add an auto-reply (enables triggers)."""
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return
    raw = command.args or ""
    if TRIGGER_SEPARATOR not in raw:
        await message.reply(_("trigger_usage"))
        return
    pattern, reply_text = raw.split(TRIGGER_SEPARATOR, 1)
    pattern, reply_text = pattern.strip(), reply_text.strip()
    if not pattern or not reply_text:
        await message.reply(_("trigger_usage"))
        return

    session: AsyncSession = data["session"]
    redis: RedisClient = data["redis"]
    if await crud.count_triggers(session, message.chat.id) >= TRIGGER_MAX_PER_CHAT:
        await message.reply(_("trigger_limit", max=TRIGGER_MAX_PER_CHAT))
        return
    added = await crud.add_trigger(session, message.chat.id, pattern, reply_text)
    if not added:
        await message.reply(_("trigger_exists", pattern=pattern.lower()))
        return
    # First trigger auto-enables the feature so it works without a second step.
    await _save(data, message.chat.id, triggers_enabled=True)
    await redis.invalidate_triggers(message.chat.id)
    await message.reply(_("trigger_added", pattern=pattern.lower()))


@router.message(Command("deltrigger"))
async def cmd_deltrigger(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return
    pattern = (command.args or "").strip()
    if not pattern:
        await message.reply(_("trigger_usage"))
        return
    session: AsyncSession = data["session"]
    redis: RedisClient = data["redis"]
    removed = await crud.remove_trigger(session, message.chat.id, pattern)
    await redis.invalidate_triggers(message.chat.id)
    key = "trigger_removed" if removed else "trigger_not_found"
    await message.reply(_(key, pattern=pattern.lower()))


@router.message(Command("triggers"))
async def cmd_triggers(
    message: types.Message, command: CommandObject, **data: Any
) -> None:
    """`/triggers` lists them; `/triggers on|off` toggles auto-replies."""
    _ = data["_"]
    if not _require_admin(data):
        await message.reply(_("error_not_admin"))
        return

    arg = (command.args or "").strip()
    if arg:
        val = _parse_onoff(arg)
        if val is None:
            await message.reply(_("trigger_usage"))
            return
        await _save(data, message.chat.id, triggers_enabled=val)
        key = "ok_enabled" if val else "ok_disabled"
        await message.reply(_(key, feature=_("feature_triggers")))
        return

    session: AsyncSession = data["session"]
    triggers = await crud.list_triggers(session, message.chat.id)
    if not triggers:
        await message.reply(_("triggers_empty"))
        return
    listing = "\n".join(f"• {t['pattern']} → {t['reply_text']}" for t in triggers)
    await message.reply(_("triggers_list", count=len(triggers), triggers=listing))
