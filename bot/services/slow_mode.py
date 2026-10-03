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
A legacy per-topic ``wl_seconds`` is ignored. Overrides are consulted only
while the chat-level row is enabled.

Fail-open by design: non-group chats, bots/anonymous senders, disabled config
and non-positive intervals are always allowed. Callers must not let slow mode
break the message pipeline.
"""

from __future__ import annotations

import logging
import time

from bot.constants import SCAM_SOURCE_VERIFIED
from bot.db import crud
from bot.utils.text import format_duration

logger = logging.getLogger(__name__)

# Per-chat key prefix: slow:{chat_id}:{topic}:{user_id}
_KEY_PREFIX = "slow:"

GROUP_TYPES = ("group", "supergroup")


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

    # (d) The chat must have slow mode enabled. «выкл» on the chat wins over
    # every per-topic override, so admins keep one global switch.
    config = await crud.get_slow_mode(data["session"], message.chat.id)
    if config is None or not config.enabled:
        return True

    # (e) A per-topic override (forum threads only) comes first: its own
    # on/off switch, and its own intervals with NULL meaning «inherit the chat
    # value». Without a row the legacy chat scope applies — a non-empty
    # topic_ids list restricts the rule to those threads, while other topics
    # and non-forum topic 0 are allowed and NOT recorded.
    topic = message.message_thread_id or 0
    override = (
        await crud.get_slow_mode_topic(data["session"], message.chat.id, topic)
        if topic
        else None
    )
    if override is not None:
        if not override.enabled:
            return True
        if override.regular_seconds is not None:
            # A topic's own value is ONE limit for everyone in it: it is not
            # split into the chat's regular/WL windows (a legacy per-topic
            # ``wl_seconds`` is ignored — the settings screen sets one number).
            regular = wl = override.regular_seconds
        else:
            regular, wl = config.regular_seconds, config.wl_seconds
    else:
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
            try:
                await message.reply(
                    data["_"]("slow_mode_blocked", wait=format_duration(remaining))
                )
            except Exception:
                pass
            return False

    # (h) Allowed: record the timestamp, expire just past the interval.
    await data["redis"].set(key, str(now), ttl=interval + 60)
    return True
