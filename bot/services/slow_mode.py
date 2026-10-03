"""Slow-mode enforcement: per-chat rate limiting by user role.

Regular users may post at most one message per ``regular_seconds``; verified
sellers (``scam_list`` source=verified) get the relaxed ``wl_seconds`` window;
admins and the owner are unlimited. State lives in Redis under
``slow:{chat_id}:{topic}:{user_id}`` as the unix timestamp of the last allowed
message; keys expire shortly after the interval so state never leaks forever.

The rule can be scoped to forum topics via ``SlowMode.topic_ids``: a
non-empty list restricts enforcement to those ``message_thread_id`` values
(``topic`` 0 for non-forum messages is NOT covered), while an empty/``None``
scope applies to the whole chat (all topics + non-forum messages).

``SlowModeTopic`` rows override that for a single thread: ``enabled=False``
exempts the topic even inside the scope, and the row's own
``regular_seconds`` sets ONE interval for everyone in that topic (verified
sellers included) instead of the chat's split; NULL inherits the chat values.
A legacy per-topic ``wl_seconds`` is ignored.

A topic row stands on its own: it is enforced even when the chat-level row is
missing or ``enabled=False`` (the chat's switch only governs topics WITHOUT a
row, so admins keep one global default without having to pre-enable it). A row
turned on with no own value falls back to the chat's values, or to the 6 h /
3 h defaults when the chat has no row at all.

Violations escalate: a blocked message is deleted and its author warned, and
once ``chat_settings.sm_warn_limit`` messages have been blocked in the same
window the configured punishment (mute/kick/ban for ``sm_punish_duration``) is
applied. The warn text is ``chat_settings.sm_warn_text`` when the owner set one,
otherwise the built-in one.

Fail-open by design: non-group chats, bots/anonymous senders, disabled config
and non-positive intervals are always allowed. Callers must not let slow mode
break the message pipeline — every step of the punishment path is best-effort.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from bot.constants import SCAM_SOURCE_VERIFIED
from bot.db import crud
from bot.utils.text import build_mention, format_duration, punish_action_label

logger = logging.getLogger(__name__)

# Per-chat key prefix: slow:{chat_id}:{topic}:{user_id}
_KEY_PREFIX = "slow:"

# Blocked-message counter per chat+topic+user (punishment escalation).
_VIOLATION_PREFIX = "slowviol:"

# Punishment actions accepted from the settings; anything else falls back to mute.
PUNISH_ACTIONS = ("mute", "kick", "ban")

GROUP_TYPES = ("group", "supergroup")

# Defaults the settings screen seeds when a chat has no row of its own; a topic
# turned on without an own value uses them too (see _effective).
DEFAULT_REGULAR_SECONDS = 21600
DEFAULT_WL_SECONDS = 10800


async def check_and_record(bot, message, data: dict) -> bool:
    """Return True if the message may pass, False if slow mode blocked it.

    A blocked message is deleted and its sender gets a ``slow_mode_blocked``
    notice (best-effort, exceptions swallowed). An allowed message records the
    current timestamp in Redis under the chat+topic+user key.

    ``data`` must carry the session, redis client and translator injected by
    the middlewares (``data["session"]``, ``data["redis"]``, ``data["_"]``).
    """
    # (a) Only group/supergroup chats are rate-limited.
    if getattr(message.chat, "type", None) not in GROUP_TYPES:
        return True

    # (b) Bots and channel/anonymous senders are never rate-limited.
    user = message.from_user
    if user is None or user.is_bot or message.sender_chat is not None:
        return True

    # (c) Admins and the owner are unlimited.
    if data.get("is_admin") or data.get("is_owner"):
        return True

    # (d) A per-topic override (forum threads only) comes first: its own on/off
    # switch and its own interval, with NULL meaning «use the chat's value». A
    # topic row is self-contained — it applies even when the chat-wide row is
    # missing or off, so a per-topic limit never depends on the chat switch.
    topic = message.message_thread_id or 0
    override = (
        await crud.get_slow_mode_topic(data["session"], message.chat.id, topic)
        if topic
        else None
    )
    # The chat row is only the default for topics WITHOUT a row of their own.
    config = await crud.get_slow_mode(data["session"], message.chat.id)
    if override is not None:
        if not override.enabled:
            return True
        if override.regular_seconds is not None:
            # A topic's own value is ONE limit for everyone in it: it is not
            # split into the chat's regular/WL windows (a legacy per-topic
            # ``wl_seconds`` is ignored — the settings screen sets one number).
            regular = wl = override.regular_seconds
        elif config is not None:
            regular, wl = config.regular_seconds, config.wl_seconds
        else:
            regular, wl = DEFAULT_REGULAR_SECONDS, DEFAULT_WL_SECONDS
    else:
        # (e) Without a row the legacy chat scope applies — a non-empty
        # topic_ids list restricts the rule to those threads, while other
        # topics and non-forum topic 0 are allowed and NOT recorded. «выкл» on
        # the chat still means «no limit anywhere a topic has no rule».
        if config is None or not config.enabled:
            return True
        if config.topic_ids and topic not in config.topic_ids:
            return True
        regular, wl = config.regular_seconds, config.wl_seconds

    if regular <= 0 and wl <= 0:
        return True

    # (f) Role decides the interval: verified sellers get the WL allowance.
    entry = await crud.get_scam_entry(data["session"], user.id)
    is_wl = entry is not None and entry.source == SCAM_SOURCE_VERIFIED
    interval = wl if is_wl else regular
    if interval <= 0:
        return True

    # (g) Enforce: at most one message per interval per chat+topic+user.
    key = f"{_KEY_PREFIX}{message.chat.id}:{topic}:{user.id}"
    now = int(time.time())
    last = await data["redis"].get(key)
    if last is not None:
        try:
            last_ts = int(float(last))
        except (TypeError, ValueError):
            last_ts = 0
        if now - last_ts < interval:
            remaining = interval - (now - last_ts)
            try:
                await bot.delete_message(message.chat.id, message.message_id)
            except Exception:
                pass
            await _warn_author(bot, message, data, remaining)
            await _count_violation(bot, message, data, interval, topic, user)
            return False

    # (h) Allowed: record the timestamp, expire just past the interval.
    await data["redis"].set(key, str(now), ttl=interval + 60)
    return True


async def _warn_author(bot, message, data: dict, remaining: int) -> None:
    """Reply to a blocked message with the owner's text, or the built-in one.

    The custom text goes out with ``parse_mode="HTML"`` (the same trust model as
    ``welcome_text``); if Telegram rejects the markup the message is retried as
    plain text so the author always sees something.
    """
    settings = data.get("settings") or {}
    text = (settings.get("sm_warn_text") or "").strip()
    try:
        if text:
            try:
                await message.reply(text, parse_mode="HTML")
            except Exception:
                await message.reply(text)
        else:
            await message.reply(
                data["_"]("slow_mode_blocked", wait=format_duration(remaining))
            )
    except Exception:
        pass


async def _count_violation(
    bot, message, data: dict, interval: int, topic: int, user
) -> None:
    """Count a blocked message; punish the author once the limit is reached.

    Violations are counted in the same window as the slowdown itself (the
    counter expires with the timestamp key), so an occasional offender starts
    clean after the interval. ``sm_warn_limit <= 0`` means «never punish».
    """
    settings = data.get("settings") or {}
    limit = _as_int(settings.get("sm_warn_limit"), 0)
    if limit <= 0:
        return
    redis = data.get("redis")
    if redis is None:
        return
    key = f"{_VIOLATION_PREFIX}{message.chat.id}:{topic}:{user.id}"
    # ``_as_int`` also keeps this honest for a client that returns something
    # unexpected: a counter we cannot read is a counter we do not punish on.
    count = _as_int(await redis.incr(key), None)
    if count is None:  # Redis error: fail open, never punish on a lost counter
        return
    if count < limit:
        if count == 1:
            await redis.expire(key, max(interval, 60) + 60)
        return
    # Limit reached: punish once and start the count over, so the next spree
    # has to earn the punishment again.
    await redis.delete(key)
    await _punish(bot, message, data, user)


async def _punish(bot, message, data: dict, user) -> None:
    """Apply the configured punishment and announce it in the chat."""
    settings = data.get("settings") or {}
    action = str(settings.get("sm_punish_action") or "mute").lower()
    if action not in PUNISH_ACTIONS:
        action = "mute"
    duration = _as_int(settings.get("sm_punish_duration"), None) or None
    session = data.get("session")
    chat_id = message.chat.id
    # Imported here: ``bot.handlers`` pulls in the routers, which import this
    # service — a module-level import would close a cycle.
    from bot.handlers.actions import do_ban, do_kick, do_mute

    reason = data["_"]("sm_punish_reason")
    try:
        if action == "kick":
            ok = await do_kick(bot, session, chat_id, bot.id, user.id, reason)
        elif action == "ban":
            ok = await do_ban(bot, session, chat_id, bot.id, user.id, duration, reason)
        else:
            ok = await do_mute(bot, session, chat_id, bot.id, user.id, duration, reason)
    except Exception:
        logger.warning("slow_mode: punishment failed", exc_info=True)
        return
    if not ok:
        return
    try:
        await bot.send_message(
            chat_id,
            data["_"](
                "sm_punish_applied",
                user=build_mention(user.id, user.full_name),
                action=punish_action_label(data["_"], action, duration),
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass


def _as_int(value: Any, default: int | None) -> int | None:
    """Tolerant int cast for settings coming from Redis/JSON."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
