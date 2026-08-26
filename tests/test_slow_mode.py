"""Tests for slow-mode persistence (Task 2) and enforcement (Task 3).

Task 2: get/set/upsert semantics of the SlowMode row.
Task 3: ``check_and_record`` + ``SlowModeMiddleware`` — per-chat rate limiting
by role (regular vs verified-seller vs admin/owner), Redis bookkeeping and
blocked-message handling.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.constants import SCAM_SOURCE_VERIFIED
from bot.db import crud
from bot.db.models import Base, Chat, SlowMode
from bot.middlewares.slow_mode import SlowModeMiddleware
from bot.services.slow_mode import check_and_record
from tests.conftest import make_bot, make_chat, make_message, make_user


@pytest.fixture
async def db_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add(
            Chat(chat_id=-1001, title="Test", type="supergroup", language="ru")
        )
        await session.commit()
        yield session
    await engine.dispose()


async def _row_count(session) -> int:
    result = await session.execute(select(func.count()).select_from(SlowMode))
    return int(result.scalar_one())


async def test_get_slow_mode_returns_none_when_absent(db_session):
    assert await crud.get_slow_mode(db_session, -1001) is None


async def test_set_slow_mode_creates_with_defaults(db_session):
    sm = await crud.set_slow_mode(db_session, -1001)
    assert sm.chat_id == -1001
    assert sm.enabled is False
    assert sm.regular_seconds == 21600
    assert sm.wl_seconds == 10800
    assert await _row_count(db_session) == 1


async def test_set_slow_mode_updates_fields(db_session):
    await crud.set_slow_mode(db_session, -1001)
    updated = await crud.set_slow_mode(
        db_session, -1001, enabled=True, regular_seconds=3600, wl_seconds=1800
    )
    assert updated.enabled is True
    assert updated.regular_seconds == 3600
    assert updated.wl_seconds == 1800
    # Partial update leaves untouched fields at their previous values.
    await crud.set_slow_mode(db_session, -1001, enabled=False)
    final = await crud.get_slow_mode(db_session, -1001)
    assert final is not None
    assert final.enabled is False
    assert final.regular_seconds == 3600
    assert final.wl_seconds == 1800


async def test_set_slow_mode_twice_keeps_one_row(db_session):
    await crud.set_slow_mode(db_session, -1001)
    await crud.set_slow_mode(db_session, -1001, enabled=True)
    assert await _row_count(db_session) == 1


async def test_set_slow_mode_stores_topic_ids(db_session):
    sm = await crud.set_slow_mode(
        db_session, -1001, enabled=True, topic_ids=[3, 6]
    )
    assert sm.topic_ids == [3, 6]


async def test_set_slow_mode_empty_topic_ids_stored_explicitly(db_session):
    # [] is a meaningful scope (whole chat) and must be stored, not skipped.
    await crud.set_slow_mode(db_session, -1001, topic_ids=[3, 6])
    sm = await crud.set_slow_mode(db_session, -1001, topic_ids=[])
    assert sm.topic_ids == []


async def test_set_slow_mode_topic_ids_none_leaves_scope_unchanged(db_session):
    await crud.set_slow_mode(db_session, -1001, topic_ids=[3, 6])
    await crud.set_slow_mode(db_session, -1001, enabled=False)
    final = await crud.get_slow_mode(db_session, -1001)
    assert final is not None
    assert final.enabled is False
    assert final.topic_ids == [3, 6]  # untouched by the partial update


# --------------------------------------------------------------------------- #
# Task 3: enforcement — check_and_record + SlowModeMiddleware
# --------------------------------------------------------------------------- #

CHAT_ID = -1001234
USER_ID = 1000
MSG_ID = 555


def _config(enabled=True, regular=21600, wl=10800, topic_ids=None) -> SimpleNamespace:
    return SimpleNamespace(
        enabled=enabled,
        regular_seconds=regular,
        wl_seconds=wl,
        topic_ids=topic_ids,
    )


def _patch_crud(monkeypatch, config, scam_entry=None):
    get_slow_mode = AsyncMock(return_value=config)
    get_scam_entry = AsyncMock(return_value=scam_entry)
    monkeypatch.setattr(crud, "get_slow_mode", get_slow_mode)
    monkeypatch.setattr(crud, "get_scam_entry", get_scam_entry)
    return get_slow_mode, get_scam_entry


def _group_message(
    user_id=USER_ID,
    chat_id=CHAT_ID,
    topic: int = 0,
    is_bot: bool = False,
    sender_chat=None,
    chat_type: str = "supergroup",
):
    msg = make_message(
        chat=make_chat(chat_id=chat_id, chat_type=chat_type),
        from_user=make_user(user_id, is_bot=is_bot),
        sender_chat=sender_chat,
    )
    msg.message_thread_id = topic  # MagicMock auto-attr is truthy; pin an int
    return msg


def _data(base_data, *, is_admin=False, is_owner=False) -> dict:
    return {**base_data, "is_admin": is_admin, "is_owner": is_owner}


async def test_non_group_chat_is_allowed_without_redis(monkeypatch, base_data):
    _patch_crud(monkeypatch, _config())
    msg = _group_message(chat_type="private")
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True
    base_data["redis"].get.assert_not_called()
    base_data["redis"].set.assert_not_called()


async def test_missing_config_is_allowed(monkeypatch, base_data):
    get_slow_mode, _ = _patch_crud(monkeypatch, None)
    msg = _group_message()
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True
    get_slow_mode.assert_awaited_once()
    base_data["redis"].set.assert_not_called()


async def test_disabled_config_is_allowed(monkeypatch, base_data):
    _patch_crud(monkeypatch, _config(enabled=False))
    msg = _group_message()
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True
    base_data["redis"].get.assert_not_called()
    base_data["redis"].set.assert_not_called()


async def test_zero_intervals_are_allowed(monkeypatch, base_data):
    _patch_crud(monkeypatch, _config(regular=0, wl=0))
    msg = _group_message()
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True
    base_data["redis"].set.assert_not_called()


async def test_admin_is_allowed_without_record(monkeypatch, base_data):
    _patch_crud(monkeypatch, _config())
    msg = _group_message()
    data = _data(base_data, is_admin=True)
    assert await check_and_record(make_bot(), msg, data) is True
    base_data["redis"].set.assert_not_called()


async def test_owner_is_allowed_without_record(monkeypatch, base_data):
    _patch_crud(monkeypatch, _config())
    msg = _group_message()
    data = _data(base_data, is_owner=True)
    assert await check_and_record(make_bot(), msg, data) is True
    base_data["redis"].set.assert_not_called()


async def test_regular_user_first_message_allowed_and_recorded(
    monkeypatch, base_data
):
    _patch_crud(monkeypatch, _config(regular=60, wl=30))
    redis = base_data["redis"]
    redis.get.return_value = None  # AsyncMock default is truthy; no prior hit
    msg = _group_message(topic=42)
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True

    redis.set.assert_awaited_once()
    args, kwargs = redis.set.await_args
    assert args[0] == f"slow:{CHAT_ID}:42:{USER_ID}"  # chat + topic + user
    assert args[1].isdigit()  # unix timestamp
    assert kwargs["ttl"] == 60 + 60  # interval + 60


# --- topic scope (slow_mode.topic_ids) ------------------------------------- #

async def test_topic_scoped_config_records_in_selected_topic(monkeypatch, base_data):
    _patch_crud(monkeypatch, _config(regular=60, wl=30, topic_ids=[3, 6]))
    redis = base_data["redis"]
    redis.get.return_value = None
    msg = _group_message(topic=3)
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True

    redis.set.assert_awaited_once()
    args, _kwargs = redis.set.await_args
    assert args[0] == f"slow:{CHAT_ID}:3:{USER_ID}"  # topic 3 covered


async def test_topic_scoped_config_allows_unselected_topic(monkeypatch, base_data):
    _patch_crud(monkeypatch, _config(regular=60, wl=30, topic_ids=[3, 6]))
    redis = base_data["redis"]
    msg = _group_message(topic=45)
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True

    redis.get.assert_not_called()
    redis.set.assert_not_called()  # outside the scope: allowed, NOT recorded


async def test_topic_scoped_config_allows_non_forum_messages(monkeypatch, base_data):
    _patch_crud(monkeypatch, _config(regular=60, wl=30, topic_ids=[3, 6]))
    redis = base_data["redis"]
    msg = _group_message(topic=0)  # non-forum message
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True

    redis.get.assert_not_called()
    redis.set.assert_not_called()  # thread_id 0 is NOT covered by a topic list


async def test_empty_topic_ids_applies_to_whole_chat(monkeypatch, base_data):
    _patch_crud(monkeypatch, _config(regular=60, wl=30, topic_ids=[]))
    redis = base_data["redis"]
    redis.get.return_value = None
    msg = _group_message(topic=42)
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True

    redis.set.assert_awaited_once()
    args, _kwargs = redis.set.await_args
    assert args[0] == f"slow:{CHAT_ID}:42:{USER_ID}"  # [] = whole chat, as before


async def test_regular_user_within_interval_is_blocked(monkeypatch, base_data):
    _patch_crud(monkeypatch, _config(regular=60, wl=30))
    redis = base_data["redis"]
    redis.get.return_value = str(int(time.time()) - 10)  # posted 10s ago
    bot = make_bot()
    msg = _group_message()
    assert await check_and_record(bot, msg, _data(base_data)) is False

    bot.delete_message.assert_awaited_once_with(CHAT_ID, MSG_ID)
    msg.reply.assert_awaited_once_with("slow_mode_blocked")
    redis.set.assert_not_called()


async def test_bot_sender_is_skipped(monkeypatch, base_data):
    _patch_crud(monkeypatch, _config())
    msg = _group_message(is_bot=True)
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True
    base_data["redis"].set.assert_not_called()


async def test_sender_chat_anonymous_is_skipped(monkeypatch, base_data):
    _patch_crud(monkeypatch, _config())
    msg = _group_message(sender_chat=SimpleNamespace(id=CHAT_ID, type="channel"))
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True
    base_data["redis"].get.assert_not_called()
    base_data["redis"].set.assert_not_called()


async def test_wl_seller_uses_wl_interval(monkeypatch, base_data):
    # regular_seconds=1 would block anyone, but a verified seller gets the
    # huge WL window and passes; a plain user with the same config is blocked.
    config = _config(regular=1, wl=999999)
    _, get_scam_entry = _patch_crud(
        monkeypatch, config, scam_entry=SimpleNamespace(source=SCAM_SOURCE_VERIFIED)
    )
    redis = base_data["redis"]
    redis.get.return_value = None
    msg = _group_message()
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True
    get_scam_entry.assert_awaited_once_with(base_data["session"], USER_ID)
    _, kwargs = redis.set.await_args
    assert kwargs["ttl"] == 999999 + 60

    # Same tiny regular interval, but a plain user (no WL entry): blocked.
    _patch_crud(monkeypatch, config, scam_entry=None)
    redis.set.reset_mock()
    redis.get.return_value = str(int(time.time()))  # 0s ago < 1s interval
    assert await check_and_record(make_bot(), msg, _data(base_data)) is False
    msg.reply.assert_awaited()
    redis.set.assert_not_called()


# --- middleware ----------------------------------------------------------- #

async def test_middleware_drops_blocked_message(monkeypatch, base_data):
    mw = SlowModeMiddleware()
    handler = AsyncMock(return_value="handled")
    check = AsyncMock(return_value=False)
    monkeypatch.setattr("bot.middlewares.slow_mode.check_and_record", check)
    data = {**base_data, "bot": make_bot()}
    assert await mw(handler, SimpleNamespace(), data) is None
    check.assert_awaited_once_with(data["bot"], ANY, data)
    handler.assert_not_awaited()


async def test_middleware_passes_allowed_message(monkeypatch, base_data):
    mw = SlowModeMiddleware()
    handler = AsyncMock(return_value="handled")
    check = AsyncMock(return_value=True)
    monkeypatch.setattr("bot.middlewares.slow_mode.check_and_record", check)
    data = {**base_data, "bot": make_bot()}
    assert await mw(handler, SimpleNamespace(), data) == "handled"
    handler.assert_awaited_once()


async def test_middleware_survives_check_errors(monkeypatch, base_data, caplog):
    mw = SlowModeMiddleware()
    handler = AsyncMock(return_value="handled")
    check = AsyncMock(side_effect=RuntimeError("redis down"))
    monkeypatch.setattr("bot.middlewares.slow_mode.check_and_record", check)
    data = {**base_data, "bot": make_bot()}
    assert await mw(handler, SimpleNamespace(), data) == "handled"
    handler.assert_awaited_once()
    assert "slow_mode.error" in caplog.text


# --- i18n ----------------------------------------------------------------- #

def test_slow_mode_blocked_key_contains_formatted_wait():
    from bot.i18n.loader import get_i18n

    ru = get_i18n().get("slow_mode_blocked", "ru", wait="6 ч")
    en = get_i18n().get("slow_mode_blocked", "en", wait="6h")
    assert "6 ч" in ru
    assert ru.startswith("⏳")
    assert "6h" in en
