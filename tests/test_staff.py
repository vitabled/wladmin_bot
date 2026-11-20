"""Tests for Phase A staff-role persistence and caching."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.config import Settings
from bot.db.models import Base, StaffMember
from bot.services.staff import (
    ROLE_ADMIN,
    ROLE_MODERATOR,
    ROLE_ORDER,
    ROLE_OWNER,
    add_staff,
    deactivate,
    get_role,
    get_role_cached,
    list_staff,
    normalize_role,
    parse_ids,
    role_at_least,
    seed_defaults,
)


@pytest.fixture
async def db_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


def _settings(**overrides) -> Settings:
    values = {
        "TELEGRAM_BOT_TOKEN": "token",
        "WEBHOOK_URL": "https://example.test/webhook",
        "WEBHOOK_SECRET": "secret",
        "OWNER_ID": 1,
        "DATABASE_URL": "postgresql+asyncpg://test:test@db/test",
    }
    values.update(overrides)
    return Settings(**values)


def test_role_helpers_normalize_compare_and_parse_ids():
    assert normalize_role(" ADMIN ") == ROLE_ADMIN
    assert normalize_role("unknown") is None
    assert normalize_role(None) is None
    assert ROLE_ORDER[ROLE_OWNER] > ROLE_ORDER[ROLE_ADMIN]
    assert ROLE_ORDER[ROLE_ADMIN] > ROLE_ORDER[ROLE_MODERATOR]
    assert role_at_least(ROLE_OWNER, ROLE_ADMIN)
    assert role_at_least(ROLE_ADMIN, ROLE_ADMIN)
    assert not role_at_least(ROLE_MODERATOR, ROLE_ADMIN)
    assert not role_at_least("unknown", ROLE_MODERATOR)
    assert parse_ids(" 10, junk, 20, ,30x, 40 ") == (10, 20, 40)


def test_settings_default_staff_ids_ignore_junk():
    assert _settings().DEFAULT_STAFF_IDS == ""
    assert _settings().default_staff_ids == ()
    settings = _settings(DEFAULT_STAFF_IDS="7176227239, junk, ,1782827633")
    assert settings.default_staff_ids == (7176227239, 1782827633)


def test_staff_model_schema():
    table = StaffMember.__table__
    assert table.name == "staff_members"
    assert table.c.user_id.primary_key
    assert table.c.user_id.autoincrement is False
    assert table.c.role.type.length == 16
    assert table.c.role.nullable is False
    assert table.c.title.type.length == 64
    assert table.c.title.nullable is True
    assert table.c.added_by.nullable is True
    assert table.c.added_at.server_default is not None
    assert table.c.is_active.nullable is False


async def test_seed_defaults_is_idempotent_and_owner_always_wins(db_session):
    db_session.add(
        StaffMember(
            user_id=1,
            role=ROLE_MODERATOR,
            added_by=99,
            is_active=False,
        )
    )
    await db_session.flush()

    await seed_defaults(db_session, 1, (2, 1, 3, 2))
    await seed_defaults(db_session, 1, (2, 1, 3, 2))

    rows = list(
        (
            await db_session.execute(
                select(StaffMember).order_by(StaffMember.user_id)
            )
        )
        .scalars()
        .all()
    )
    assert [(row.user_id, row.role, row.is_active) for row in rows] == [
        (1, ROLE_OWNER, True),
        (2, ROLE_ADMIN, True),
        (3, ROLE_ADMIN, True),
    ]
    assert rows[0].added_by == 99


async def test_get_role_only_returns_active_staff(db_session):
    db_session.add_all(
        [
            StaffMember(user_id=10, role=ROLE_ADMIN, is_active=True),
            StaffMember(user_id=20, role=ROLE_MODERATOR, is_active=False),
        ]
    )
    await db_session.flush()

    assert await get_role(db_session, 10) == ROLE_ADMIN
    assert await get_role(db_session, 20) is None
    assert await get_role(db_session, 30) is None


async def test_get_role_cached_uses_positive_cache_without_database():
    session = AsyncMock()
    redis = AsyncMock()
    redis.get.return_value = ROLE_ADMIN

    assert await get_role_cached(session, redis, 10) == ROLE_ADMIN
    redis.get.assert_awaited_once_with("staff:10")
    session.get.assert_not_awaited()
    redis.set.assert_not_awaited()


async def test_get_role_cached_negative_caches_database_miss():
    session = AsyncMock()
    session.get.return_value = None
    redis = AsyncMock()
    redis.get.side_effect = [None, "-"]

    assert await get_role_cached(session, redis, 10) is None
    assert await get_role_cached(session, redis, 10) is None
    session.get.assert_awaited_once_with(StaffMember, 10)
    redis.set.assert_awaited_once_with("staff:10", "-", ttl=60)


async def test_get_role_cached_caches_database_role():
    session = AsyncMock()
    session.get.return_value = StaffMember(
        user_id=10, role=ROLE_MODERATOR, is_active=True
    )
    redis = AsyncMock()
    redis.get.return_value = None

    assert await get_role_cached(session, redis, 10) == ROLE_MODERATOR
    redis.set.assert_awaited_once_with(
        "staff:10", ROLE_MODERATOR, ttl=60
    )


async def test_add_staff_creates_updates_and_invalidates_cache(db_session):
    redis = AsyncMock()
    member = await add_staff(
        db_session,
        10,
        " ADMIN ",
        added_by=1,
        title="Operator",
        redis=redis,
    )
    assert (member.role, member.title, member.added_by, member.is_active) == (
        ROLE_ADMIN,
        "Operator",
        1,
        True,
    )
    redis.delete.assert_awaited_once_with("staff:10")

    redis.reset_mock()
    updated = await add_staff(
        db_session,
        10,
        ROLE_MODERATOR,
        added_by=2,
        title=None,
        redis=redis,
    )
    assert updated is member
    assert (updated.role, updated.title, updated.added_by, updated.is_active) == (
        ROLE_MODERATOR,
        None,
        2,
        True,
    )
    redis.delete.assert_awaited_once_with("staff:10")


async def test_add_staff_rejects_unknown_role(db_session):
    with pytest.raises(ValueError, match="Unknown staff role"):
        await add_staff(db_session, 10, "superadmin", added_by=1)


async def test_deactivate_is_idempotent_and_invalidates_on_change(db_session):
    redis = AsyncMock()
    await add_staff(db_session, 10, ROLE_ADMIN, added_by=1)

    assert await deactivate(db_session, 10, redis=redis) is True
    assert await get_role(db_session, 10) is None
    redis.delete.assert_awaited_once_with("staff:10")

    redis.reset_mock()
    assert await deactivate(db_session, 10, redis=redis) is False
    assert await deactivate(db_session, 999, redis=redis) is False
    redis.delete.assert_not_awaited()


async def test_list_staff_returns_active_members_in_role_order(db_session):
    db_session.add_all(
        [
            StaffMember(user_id=30, role=ROLE_MODERATOR, is_active=True),
            StaffMember(user_id=10, role=ROLE_OWNER, is_active=True),
            StaffMember(user_id=20, role=ROLE_ADMIN, is_active=True),
            StaffMember(user_id=40, role=ROLE_OWNER, is_active=False),
        ]
    )
    await db_session.flush()

    rows = await list_staff(db_session)
    assert [(row.user_id, row.role) for row in rows] == [
        (10, ROLE_OWNER),
        (20, ROLE_ADMIN),
        (30, ROLE_MODERATOR),
    ]
