"""Bot-wide staff roles: registry, ordering, seeds and Redis cache (Phase A).

Staff membership is the source of truth for private admin surfaces and is
kept separate from per-chat Telegram administrator status. Roles are totally
ordered ``owner`` > ``admin`` > ``moderator``; seeds from env (``OWNER_ID`` and
``DEFAULT_STAFF_IDS``) are applied idempotently at startup.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import StaffMember

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MODERATOR = "moderator"

# Higher value == more privileged. Unknown roles rank below every real role.
ROLE_ORDER: dict[str, int] = {
    ROLE_OWNER: 3,
    ROLE_ADMIN: 2,
    ROLE_MODERATOR: 1,
}

# Sentinel stored in Redis for "no role" so a cache hit is distinguishable
# from a cache miss (which must query the database).
_NEGATIVE = "-"

# Roles are cached briefly so hot update paths avoid a query per event.
ROLE_CACHE_TTL = 60

_CACHE_PREFIX = "staff"


def _cache_key(user_id: int) -> str:
    return f"{_CACHE_PREFIX}:{user_id}"


def normalize_role(value: str | None) -> str | None:
    """Trim/lower a role name; return None when it is unknown."""
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized if normalized in ROLE_ORDER else None


def role_at_least(role: str | None, required: str) -> bool:
    """True when ``role`` is at least as privileged as ``required``."""
    have = ROLE_ORDER.get(role or "", 0)
    need = ROLE_ORDER.get(required, 0)
    return have >= need


def parse_ids(csv_value: str | None) -> tuple[int, ...]:
    """Parse a comma-separated id list, skipping blank/invalid parts."""
    ids: list[int] = []
    for part in (csv_value or "").split(","):
        token = part.strip()
        if not token:
            continue
        try:
            ids.append(int(token))
        except ValueError:
            continue
    return tuple(ids)


async def get_role(session: AsyncSession, user_id: int) -> str | None:
    """Active staff role for ``user_id`` from the database, else None."""
    member = await session.get(StaffMember, user_id)
    if member is None or not member.is_active:
        return None
    return member.role


async def get_role_cached(
    session: AsyncSession, redis: Any, user_id: int
) -> str | None:
    """Resolve a role through the 60-second Redis cache.

    Positive roles and database misses (cached as ``-``) are both cached so a
    hot path avoids a query per update.
    """
    key = _cache_key(user_id)
    cached = await redis.get(key)
    if cached is not None:
        return None if cached == _NEGATIVE else cached

    role = await get_role(session, user_id)
    await redis.set(key, role if role is not None else _NEGATIVE, ttl=ROLE_CACHE_TTL)
    return role


async def _invalidate(redis: Any | None, user_id: int) -> None:
    if redis is not None:
        await redis.delete(_cache_key(user_id))


async def seed_defaults(
    session: AsyncSession,
    owner_id: int,
    admin_ids: tuple[int, ...] = (),
    *,
    redis: Any | None = None,
) -> None:
    """Idempotently ensure owner/admin staff rows exist (owner always wins).

    The owner is applied last so a colliding default admin id can never demote
    the owner.
    """
    desired: dict[int, str] = {}
    for user_id in admin_ids:
        desired[user_id] = ROLE_ADMIN
    desired[owner_id] = ROLE_OWNER

    for user_id, role in desired.items():
        member = await session.get(StaffMember, user_id)
        if member is None:
            session.add(StaffMember(user_id=user_id, role=role, is_active=True))
        else:
            member.role = role
            member.is_active = True
        await _invalidate(redis, user_id)
    await session.flush()


async def add_staff(
    session: AsyncSession,
    user_id: int,
    role: str,
    added_by: int,
    title: str | None = None,
    redis: Any | None = None,
) -> StaffMember:
    """Create or update a staff member; cache is invalidated on change."""
    normalized = normalize_role(role)
    if normalized is None:
        raise ValueError(f"Unknown staff role: {role!r}")

    member = await session.get(StaffMember, user_id)
    if member is None:
        member = StaffMember(user_id=user_id, role=normalized, is_active=True)
        session.add(member)
    member.role = normalized
    member.title = title
    member.added_by = added_by
    member.is_active = True
    await session.flush()
    await _invalidate(redis, user_id)
    return member


async def deactivate(
    session: AsyncSession, user_id: int, redis: Any | None = None
) -> bool:
    """Soft-disable an active member; return False when already absent/inactive."""
    member = await session.get(StaffMember, user_id)
    if member is None or not member.is_active:
        return False
    member.is_active = False
    await session.flush()
    await _invalidate(redis, user_id)
    return True


async def list_staff(session: AsyncSession) -> list[StaffMember]:
    """Active staff ordered by privilege (owner, admin, moderator)."""
    order = case(ROLE_ORDER, value=StaffMember.role, else_=len(ROLE_ORDER) + 1)
    result = await session.execute(
        select(StaffMember)
        .where(StaffMember.is_active.is_(True))
        .order_by(order.desc(), StaffMember.user_id)
    )
    return list(result.scalars().all())
