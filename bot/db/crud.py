"""Async data-access layer (repository functions) over SQLAlchemy models.

Хендлеры не пишут ORM-запросы напрямую — только через эти функции, что
даёт единую точку для транзакций, блокировок и кэш-инвалидации.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot.constants import DEFAULT_LANGUAGE
from bot.db.models import Chat, ChatSettings, ModLog, Stopword, User, Warn

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
