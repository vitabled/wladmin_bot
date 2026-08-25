"""Async data-access layer (repository functions) over SQLAlchemy models.

Хендлеры не пишут ORM-запросы напрямую — только через эти функции, что
даёт единую точку для транзакций, блокировок и кэш-инвалидации.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot.constants import (
    DEFAULT_LANGUAGE,
    TRIGGER_CONTAINS,
    TRIGGER_PATTERN_MAX,
    TRIGGER_REPLY_MAX,
)
from bot.db.models import (
    Activity,
    Chat,
    ChatSettings,
    Federation,
    FederationBan,
    FederationChat,
    ModLog,
    ScamList,
    ScheduledPost,
    Stopword,
    Trigger,
    User,
    Warn,
)

_INT32_MOD = 2_147_483_647

# Columns clients are allowed to update via update_settings — guards against
# a caller smuggling arbitrary attribute names into the ORM object.
_SETTINGS_FIELDS = frozenset(
    {
        "welcome_enabled",
        "welcome_text",
        "delete_service_messages",
        "delete_welcome_after",
        "captcha_enabled",
        "captcha_type",
        "captcha_timeout",
        "captcha_fail_action",
        "warn_limit",
        "warn_action",
        "warn_action_duration",
        "filter_links",
        "filter_forwards",
        "filter_stopwords",
        "antispam_action",
        "antispam_exempt_admins",
        "antiflood_enabled",
        "antiflood_limit",
        "antiflood_window",
        "antiflood_action",
        "newbie_media_enabled",
        "newbie_period",
        "triggers_enabled",
        "stats_enabled",
    }
)


# --------------------------------------------------------------------------- #
# Chats & settings
# --------------------------------------------------------------------------- #
async def ensure_chat(
    session: AsyncSession, chat_id: int, title: str, chat_type: str
) -> Chat:
    """Upsert a chat row (auto-registration when the bot is added)."""
    stmt = (
        pg_insert(Chat)
        .values(
            chat_id=chat_id,
            title=title[:255],
            type=chat_type,
            language=DEFAULT_LANGUAGE,
            is_active=True,
        )
        .on_conflict_do_update(
            index_elements=[Chat.chat_id],
            set_={"title": title[:255], "type": chat_type, "is_active": True},
        )
        .returning(Chat)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


async def set_chat_active(session: AsyncSession, chat_id: int, is_active: bool) -> None:
    """Mark a chat active/inactive (bot removed / re-added)."""
    chat = await session.get(Chat, chat_id)
    if chat is not None:
        chat.is_active = is_active


async def get_chat(session: AsyncSession, chat_id: int) -> Chat | None:
    """Fetch a chat row by id (or None)."""
    return await session.get(Chat, chat_id)


async def list_active_chats(session: AsyncSession) -> list[Chat]:
    """List active chats (for the web dashboard), alphabetical by title."""
    result = await session.execute(
        select(Chat).where(Chat.is_active.is_(True)).order_by(Chat.title)
    )
    return list(result.scalars().all())


async def recent_mod_logs(
    session: AsyncSession, chat_id: int, limit: int = 20
) -> list[ModLog]:
    """Return a chat's most recent moderation-log entries (newest first)."""
    result = await session.execute(
        select(ModLog)
        .where(ModLog.chat_id == chat_id)
        .order_by(ModLog.created_at.desc(), ModLog.id.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_or_create_settings(session: AsyncSession, chat_id: int) -> ChatSettings:
    """Return chat settings, creating a defaults row if missing.

    Requires the chat row to exist (FK). ``ensure_chat`` is called by the
    settings middleware before this on every group update.
    """
    settings = await session.get(ChatSettings, chat_id)
    if settings is None:
        settings = ChatSettings(chat_id=chat_id)
        session.add(settings)
        await session.flush()
    return settings


async def update_settings(
    session: AsyncSession, chat_id: int, **fields: Any
) -> ChatSettings:
    """Update whitelisted settings fields; unknown keys raise ValueError."""
    unknown = set(fields) - _SETTINGS_FIELDS
    if unknown:
        raise ValueError(f"Unknown settings fields: {sorted(unknown)}")
    settings = await get_or_create_settings(session, chat_id)
    for key, value in fields.items():
        setattr(settings, key, value)
    await session.flush()
    return settings


def settings_to_dict(settings: ChatSettings) -> dict[str, Any]:
    """Serialize settings for Redis caching (JSON-safe)."""
    return {f: getattr(settings, f) for f in _SETTINGS_FIELDS}


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    """Look up a cached user by @username (case-insensitive, no @)."""
    normalized = username.lstrip("@").lower()
    if not normalized:
        return None
    result = await session.execute(
        select(User).where(func.lower(User.username) == normalized).limit(1)
    )
    return result.scalar_one_or_none()


async def get_users_by_ids(
    session: AsyncSession, user_ids: list[int]
) -> dict[int, str]:
    """Map user_id -> first_name for the given ids (cached users only)."""
    if not user_ids:
        return {}
    result = await session.execute(
        select(User.user_id, User.first_name).where(User.user_id.in_(user_ids))
    )
    return {int(uid): name for uid, name in result.all()}


async def upsert_user(
    session: AsyncSession,
    user_id: int,
    first_name: str,
    username: str | None,
) -> None:
    """Insert-or-update a user's cached profile (last_seen bumped)."""
    stmt = (
        pg_insert(User)
        .values(
            user_id=user_id,
            first_name=(first_name or "")[:255],
            username=username,
        )
        .on_conflict_do_update(
            index_elements=[User.user_id],
            set_={
                "first_name": (first_name or "")[:255],
                "username": username,
                "last_seen": func.now(),
            },
        )
    )
    await session.execute(stmt)


# --------------------------------------------------------------------------- #
# Warns
# --------------------------------------------------------------------------- #
async def _advisory_lock_warn(
    session: AsyncSession, chat_id: int, user_id: int
) -> None:
    """Serialize concurrent warn ops for one (chat, user) within the tx."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:k1, :k2)"),
        {"k1": chat_id % _INT32_MOD, "k2": user_id % _INT32_MOD},
    )


async def add_warn(
    session: AsyncSession,
    chat_id: int,
    user_id: int,
    admin_id: int,
    reason: str | None,
) -> int:
    """Add an active warn and return the resulting active-warn count.

    Uses a transaction-scoped advisory lock so two simultaneous ``/warn``
    calls cannot both read a stale count (ТЗ concurrency requirement).
    """
    await _advisory_lock_warn(session, chat_id, user_id)
    session.add(
        Warn(
            chat_id=chat_id,
            user_id=user_id,
            admin_id=admin_id,
            reason=reason,
            is_active=True,
        )
    )
    await session.flush()
    return await count_active_warns(session, chat_id, user_id)


async def count_active_warns(session: AsyncSession, chat_id: int, user_id: int) -> int:
    """Count a user's active warns in a chat."""
    result = await session.execute(
        select(func.count())
        .select_from(Warn)
        .where(
            Warn.chat_id == chat_id,
            Warn.user_id == user_id,
            Warn.is_active.is_(True),
        )
    )
    return int(result.scalar_one())


async def list_active_warns(
    session: AsyncSession, chat_id: int, user_id: int
) -> list[Warn]:
    """List a user's active warns (newest first)."""
    result = await session.execute(
        select(Warn)
        .where(
            Warn.chat_id == chat_id,
            Warn.user_id == user_id,
            Warn.is_active.is_(True),
        )
        .order_by(Warn.created_at.desc(), Warn.id.desc())
    )
    return list(result.scalars().all())


async def deactivate_last_warn(
    session: AsyncSession, chat_id: int, user_id: int
) -> bool:
    """Deactivate the most recent active warn. False if none existed."""
    await _advisory_lock_warn(session, chat_id, user_id)
    result = await session.execute(
        select(Warn)
        .where(
            Warn.chat_id == chat_id,
            Warn.user_id == user_id,
            Warn.is_active.is_(True),
        )
        .order_by(Warn.created_at.desc(), Warn.id.desc())
        .limit(1)
    )
    warn = result.scalar_one_or_none()
    if warn is None:
        return False
    warn.is_active = False
    await session.flush()
    return True


async def deactivate_all_warns(
    session: AsyncSession, chat_id: int, user_id: int
) -> int:
    """Deactivate all active warns for a user; returns how many were cleared."""
    warns = await list_active_warns(session, chat_id, user_id)
    for warn in warns:
        warn.is_active = False
    await session.flush()
    return len(warns)


# --------------------------------------------------------------------------- #
# Stopwords
# --------------------------------------------------------------------------- #
async def add_stopword(session: AsyncSession, chat_id: int, word: str) -> bool:
    """Add a stopword (case-insensitive dedupe). False if already present."""
    normalized = word.strip().lower()
    if not normalized:
        return False
    stmt = (
        pg_insert(Stopword)
        .values(chat_id=chat_id, word=normalized)
        .on_conflict_do_nothing(constraint="uq_stopwords_chat_word")
    )
    result = await session.execute(stmt)
    return bool(result.rowcount)


async def remove_stopword(session: AsyncSession, chat_id: int, word: str) -> bool:
    """Remove a stopword. False if it did not exist."""
    normalized = word.strip().lower()
    result = await session.execute(
        delete(Stopword).where(Stopword.chat_id == chat_id, Stopword.word == normalized)
    )
    return bool(result.rowcount)


async def list_stopwords(session: AsyncSession, chat_id: int) -> list[str]:
    """List stopwords for a chat (alphabetical)."""
    result = await session.execute(
        select(Stopword.word).where(Stopword.chat_id == chat_id).order_by(Stopword.word)
    )
    return list(result.scalars().all())


# --------------------------------------------------------------------------- #
# Triggers / auto-replies (Phase 3)
# --------------------------------------------------------------------------- #
async def add_trigger(
    session: AsyncSession,
    chat_id: int,
    pattern: str,
    reply_text: str,
    match_type: str = TRIGGER_CONTAINS,
) -> bool:
    """Add a trigger (pattern is case-insensitively deduped). False if present."""
    normalized = pattern.strip().lower()
    if not normalized or not reply_text.strip():
        return False
    stmt = (
        pg_insert(Trigger)
        .values(
            chat_id=chat_id,
            pattern=normalized[:TRIGGER_PATTERN_MAX],
            match_type=match_type,
            reply_text=reply_text[:TRIGGER_REPLY_MAX],
        )
        .on_conflict_do_nothing(constraint="uq_triggers_chat_pattern")
    )
    result = await session.execute(stmt)
    return bool(result.rowcount)


async def remove_trigger(session: AsyncSession, chat_id: int, pattern: str) -> bool:
    """Remove a trigger by pattern. False if it did not exist."""
    normalized = pattern.strip().lower()
    result = await session.execute(
        delete(Trigger).where(Trigger.chat_id == chat_id, Trigger.pattern == normalized)
    )
    return bool(result.rowcount)


async def list_triggers(session: AsyncSession, chat_id: int) -> list[dict[str, str]]:
    """List a chat's triggers (alphabetical) as JSON-serializable dicts."""
    result = await session.execute(
        select(Trigger).where(Trigger.chat_id == chat_id).order_by(Trigger.pattern)
    )
    return [
        {
            "pattern": t.pattern,
            "match_type": t.match_type,
            "reply_text": t.reply_text,
        }
        for t in result.scalars().all()
    ]


async def count_triggers(session: AsyncSession, chat_id: int) -> int:
    """Count a chat's triggers (for the per-chat limit check)."""
    result = await session.execute(
        select(func.count()).select_from(Trigger).where(Trigger.chat_id == chat_id)
    )
    return int(result.scalar_one())


# --------------------------------------------------------------------------- #
# Activity statistics (Phase 4)
# --------------------------------------------------------------------------- #
async def bump_activity(session: AsyncSession, chat_id: int, user_id: int) -> None:
    """Increment a user's message counter for a chat (upsert)."""
    stmt = (
        pg_insert(Activity)
        .values(chat_id=chat_id, user_id=user_id, message_count=1)
        .on_conflict_do_update(
            index_elements=[Activity.chat_id, Activity.user_id],
            set_={
                "message_count": Activity.message_count + 1,
                "last_active_at": func.now(),
            },
        )
    )
    await session.execute(stmt)


async def get_activity(session: AsyncSession, chat_id: int, user_id: int) -> int:
    """Return a user's message count in a chat (0 if none)."""
    result = await session.execute(
        select(Activity.message_count).where(
            Activity.chat_id == chat_id, Activity.user_id == user_id
        )
    )
    value = result.scalar_one_or_none()
    return int(value) if value is not None else 0


async def top_active(
    session: AsyncSession, chat_id: int, limit: int
) -> list[tuple[int, int]]:
    """Return the most active users as ``(user_id, message_count)`` pairs."""
    result = await session.execute(
        select(Activity.user_id, Activity.message_count)
        .where(Activity.chat_id == chat_id)
        .order_by(Activity.message_count.desc(), Activity.user_id)
        .limit(limit)
    )
    return [(int(uid), int(cnt)) for uid, cnt in result.all()]


async def chat_activity_totals(session: AsyncSession, chat_id: int) -> tuple[int, int]:
    """Return ``(total_messages, tracked_users)`` for a chat."""
    result = await session.execute(
        select(
            func.coalesce(func.sum(Activity.message_count), 0),
            func.count(),
        )
        .select_from(Activity)
        .where(Activity.chat_id == chat_id)
    )
    total, users = result.one()
    return int(total), int(users)


# --------------------------------------------------------------------------- #
# Scheduled posts (Phase 5)
# --------------------------------------------------------------------------- #
async def add_scheduled_post(
    session: AsyncSession,
    chat_id: int,
    text_body: str,
    run_at: datetime,
    interval_seconds: int | None,
    created_by: int,
) -> ScheduledPost:
    """Create a scheduled post and return it (with its generated id)."""
    post = ScheduledPost(
        chat_id=chat_id,
        text=text_body,
        run_at=run_at,
        interval_seconds=interval_seconds,
        created_by=created_by,
        enabled=True,
    )
    session.add(post)
    await session.flush()
    return post


async def list_scheduled_posts(
    session: AsyncSession, chat_id: int
) -> list[ScheduledPost]:
    """List a chat's active scheduled posts (soonest first)."""
    result = await session.execute(
        select(ScheduledPost)
        .where(ScheduledPost.chat_id == chat_id, ScheduledPost.enabled.is_(True))
        .order_by(ScheduledPost.run_at)
    )
    return list(result.scalars().all())


async def count_scheduled_posts(session: AsyncSession, chat_id: int) -> int:
    """Count a chat's active scheduled posts (for the per-chat limit)."""
    result = await session.execute(
        select(func.count())
        .select_from(ScheduledPost)
        .where(ScheduledPost.chat_id == chat_id, ScheduledPost.enabled.is_(True))
    )
    return int(result.scalar_one())


async def remove_scheduled_post(
    session: AsyncSession, chat_id: int, post_id: int
) -> bool:
    """Delete a scheduled post by id within a chat. False if not found."""
    result = await session.execute(
        delete(ScheduledPost).where(
            ScheduledPost.id == post_id, ScheduledPost.chat_id == chat_id
        )
    )
    return bool(result.rowcount)


async def due_scheduled_posts(
    session: AsyncSession, now: datetime, limit: int = 100
) -> list[ScheduledPost]:
    """Return enabled posts whose run time has arrived (locked FOR UPDATE).

    ``SKIP LOCKED`` lets multiple worker instances coexist without double-posting
    the same row.
    """
    result = await session.execute(
        select(ScheduledPost)
        .where(
            ScheduledPost.enabled.is_(True),
            ScheduledPost.run_at <= now,
        )
        .order_by(ScheduledPost.run_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list(result.scalars().all())


async def mark_post_ran(
    session: AsyncSession,
    post: ScheduledPost,
    now: datetime,
    next_run_at: datetime | None,
) -> None:
    """Advance a post after firing: reschedule if recurring, else disable."""
    post.last_run_at = now
    if next_run_at is None:
        post.enabled = False
    else:
        post.run_at = next_run_at
    await session.flush()


# --------------------------------------------------------------------------- #
# Federations (Phase 8)
# --------------------------------------------------------------------------- #
async def create_federation(
    session: AsyncSession, name: str, owner_id: int
) -> Federation | None:
    """Create a federation; None if the name is already taken."""
    existing = await session.execute(select(Federation).where(Federation.name == name))
    if existing.scalar_one_or_none() is not None:
        return None
    fed = Federation(name=name, owner_id=owner_id)
    session.add(fed)
    await session.flush()
    return fed


async def get_federation(session: AsyncSession, fed_id: int) -> Federation | None:
    """Fetch a federation by id."""
    return await session.get(Federation, fed_id)


async def get_chat_federation(session: AsyncSession, chat_id: int) -> Federation | None:
    """Return the federation a chat belongs to, or None."""
    result = await session.execute(
        select(Federation)
        .join(FederationChat, FederationChat.federation_id == Federation.id)
        .where(FederationChat.chat_id == chat_id)
    )
    return result.scalar_one_or_none()


async def add_chat_to_federation(
    session: AsyncSession, fed_id: int, chat_id: int
) -> bool:
    """Link a chat to a federation; False if it already belongs to one."""
    stmt = (
        pg_insert(FederationChat)
        .values(federation_id=fed_id, chat_id=chat_id)
        .on_conflict_do_nothing(constraint="uq_federation_chats_chat")
    )
    result = await session.execute(stmt)
    return bool(result.rowcount)


async def remove_chat_from_federation(session: AsyncSession, chat_id: int) -> bool:
    """Unlink a chat from its federation. False if it wasn't in one."""
    result = await session.execute(
        delete(FederationChat).where(FederationChat.chat_id == chat_id)
    )
    return bool(result.rowcount)


async def list_federation_chats(session: AsyncSession, fed_id: int) -> list[int]:
    """Return the chat ids belonging to a federation."""
    result = await session.execute(
        select(FederationChat.chat_id).where(FederationChat.federation_id == fed_id)
    )
    return [int(cid) for cid in result.scalars().all()]


async def count_federation_chats(session: AsyncSession, fed_id: int) -> int:
    """Count chats in a federation."""
    result = await session.execute(
        select(func.count())
        .select_from(FederationChat)
        .where(FederationChat.federation_id == fed_id)
    )
    return int(result.scalar_one())


async def add_fedban(
    session: AsyncSession,
    fed_id: int,
    user_id: int,
    reason: str | None,
    banned_by: int,
) -> bool:
    """Record a federation ban; False if already banned."""
    stmt = (
        pg_insert(FederationBan)
        .values(
            federation_id=fed_id,
            user_id=user_id,
            reason=reason,
            banned_by=banned_by,
        )
        .on_conflict_do_nothing(
            index_elements=[FederationBan.federation_id, FederationBan.user_id]
        )
    )
    result = await session.execute(stmt)
    return bool(result.rowcount)


async def remove_fedban(session: AsyncSession, fed_id: int, user_id: int) -> bool:
    """Remove a federation ban. False if it wasn't banned."""
    result = await session.execute(
        delete(FederationBan).where(
            FederationBan.federation_id == fed_id,
            FederationBan.user_id == user_id,
        )
    )
    return bool(result.rowcount)


async def is_fedbanned(session: AsyncSession, fed_id: int, user_id: int) -> bool:
    """True if the user is federation-banned."""
    result = await session.execute(
        select(FederationBan.user_id).where(
            FederationBan.federation_id == fed_id,
            FederationBan.user_id == user_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def count_fedbans(session: AsyncSession, fed_id: int) -> int:
    """Count federation bans."""
    result = await session.execute(
        select(func.count())
        .select_from(FederationBan)
        .where(FederationBan.federation_id == fed_id)
    )
    return int(result.scalar_one())


# --------------------------------------------------------------------------- #
# Scam list / seller reputation (Phase 9)
# --------------------------------------------------------------------------- #
async def get_scam_entry(
    session: AsyncSession, user_id: int
) -> ScamList | None:
    """Return a user's reputation record (scam/verified/manual), or None."""
    result = await session.execute(
        select(ScamList).where(ScamList.user_id == user_id).limit(1)
    )
    return result.scalar_one_or_none()


async def upsert_scam_entry(
    session: AsyncSession,
    user_id: int,
    source: str,
    reason: str | None = None,
) -> None:
    """Insert or overwrite a user's reputation record (unique per user_id)."""
    stmt = (
        pg_insert(ScamList)
        .values(user_id=user_id, source=source, reason=reason)
        .on_conflict_do_update(
            index_elements=[ScamList.user_id],
            set_={"source": source, "reason": reason},
        )
    )
    await session.execute(stmt)


async def remove_scam_entry(session: AsyncSession, user_id: int) -> bool:
    """Delete a user's reputation record. False if it didn't exist."""
    result = await session.execute(
        delete(ScamList).where(ScamList.user_id == user_id)
    )
    return bool(result.rowcount)


# --------------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------------- #
async def add_mod_log(
    session: AsyncSession,
    chat_id: int,
    actor_id: int,
    target_id: int | None,
    action: str,
    reason: str | None = None,
    duration: int | None = None,
) -> None:
    """Append an entry to the moderation audit log."""
    session.add(
        ModLog(
            chat_id=chat_id,
            actor_id=actor_id,
            target_id=target_id,
            action=action,
            reason=reason,
            duration=duration,
        )
    )
    await session.flush()
