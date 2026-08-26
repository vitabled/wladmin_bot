"""Tests for forum-topic tracking (Task 4): crud upsert + antispam hook."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.db import crud
from bot.db.models import Base, Chat, ChatTopic
from bot.handlers import antispam
from tests.conftest import make_chat, make_message


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
    result = await session.execute(select(func.count()).select_from(ChatTopic))
    return int(result.scalar_one())


# --------------------------------------------------------------------------- #
# crud.record_topic_seen / list_topics
# --------------------------------------------------------------------------- #
async def test_record_topic_seen_creates_then_increments(db_session):
    first = await crud.record_topic_seen(db_session, -1001, 42)
    assert first.message_count == 1
    second = await crud.record_topic_seen(db_session, -1001, 42)
    assert second.message_count == 2
    third = await crud.record_topic_seen(db_session, -1001, 42)
    assert third.message_count == 3
    # Upsert: still exactly one row for the (chat, thread) pair.
    assert await _row_count(db_session) == 1


async def test_record_topic_seen_distinct_threads(db_session):
    await crud.record_topic_seen(db_session, -1001, 1)
    await crud.record_topic_seen(db_session, -1001, 2)
    assert await _row_count(db_session) == 2


async def test_list_topics_ordered_by_last_seen_desc(db_session):
    # Explicit last_seen at insert time (server_default only fires when the
    # column is omitted) makes the ordering deterministic.
    db_session.add_all(
        [
            ChatTopic(
                chat_id=-1001,
                thread_id=1,
                message_count=1,
                last_seen=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            ChatTopic(
                chat_id=-1001,
                thread_id=2,
                message_count=5,
                last_seen=datetime(2026, 1, 3, tzinfo=UTC),
            ),
            ChatTopic(
                chat_id=-1001,
                thread_id=3,
                message_count=2,
                last_seen=datetime(2026, 1, 2, tzinfo=UTC),
            ),
        ]
    )
    await db_session.flush()
    topics = await crud.list_topics(db_session, -1001)
    assert [t.thread_id for t in topics] == [2, 3, 1]
    assert topics[0].message_count == 5


async def test_list_topics_empty(db_session):
    assert await crud.list_topics(db_session, -1001) == []


# --------------------------------------------------------------------------- #
# Antispam catch-all hook
# --------------------------------------------------------------------------- #
def _mock_pipeline(monkeypatch):
    """Isolate on_message to the recording hook (mocks the rest of the flow)."""
    process = AsyncMock(return_value=False)
    monkeypatch.setattr(antispam, "_process", process)
    monkeypatch.setattr(antispam.stats, "record_activity", AsyncMock())
    monkeypatch.setattr(
        antispam.scam, "maybe_warn_newbie", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(antispam.triggers, "maybe_reply", AsyncMock())
    return process


async def test_hook_records_forum_message(base_data, monkeypatch):
    _mock_pipeline(monkeypatch)
    record = AsyncMock()
    monkeypatch.setattr(crud, "record_topic_seen", record)
    msg = make_message(chat=make_chat(-1001, "supergroup"))
    msg.message_thread_id = 42

    await antispam.on_message(msg, **base_data)

    record.assert_awaited_once_with(base_data["session"], -1001, 42)


async def test_hook_skips_non_forum_message(base_data, monkeypatch):
    _mock_pipeline(monkeypatch)
    record = AsyncMock()
    monkeypatch.setattr(crud, "record_topic_seen", record)
    msg = make_message(chat=make_chat(-1001, "supergroup"))
    msg.message_thread_id = None

    await antispam.on_message(msg, **base_data)

    record.assert_not_awaited()


async def test_hook_skips_non_group_message(base_data, monkeypatch):
    _mock_pipeline(monkeypatch)
    record = AsyncMock()
    monkeypatch.setattr(crud, "record_topic_seen", record)
    msg = make_message(chat=make_chat(-1001, "private"))
    msg.message_thread_id = 42

    await antispam.on_message(msg, **base_data)

    record.assert_not_awaited()


async def test_hook_failure_does_not_break_pipeline(base_data, monkeypatch):
    process = _mock_pipeline(monkeypatch)
    monkeypatch.setattr(
        crud, "record_topic_seen", AsyncMock(side_effect=Exception("db down"))
    )
    msg = make_message(chat=make_chat(-1001, "supergroup"))
    msg.message_thread_id = 42

    # Must not raise; the rest of the pipeline still runs.
    await antispam.on_message(msg, **base_data)
    process.assert_awaited_once()
