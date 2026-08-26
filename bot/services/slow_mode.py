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

    # (d) The chat must have slow mode enabled with a usable interval.
    config = await crud.get_slow_mode(data["session"], message.chat.id)
    if config is None or not config.enabled:
        return True
    if config.regular_seconds <= 0 and config.wl_seconds <= 0:
        return True

    # (e) Topic scope: a non-empty topic_ids list restricts the rule to those
    # threads; anything else (other topics, non-forum topic 0) is allowed and
    # NOT recorded. Empty/None scope = whole chat, as before.
    if config.topic_ids:
        topic = message.message_thread_id or 0
        if topic not in config.topic_ids:
            return True

    # (f) Role decides the interval: verified sellers get the WL allowance.
    entry = await crud.get_scam_entry(data["session"], user.id)
    is_wl = entry is not None and entry.source == SCAM_SOURCE_VERIFIED
    interval = config.wl_seconds if is_wl else config.regular_seconds
    if interval <= 0:
        return True

    # (g) Enforce: at most one message per interval per chat+topic+user.
    topic = message.message_thread_id or 0
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
