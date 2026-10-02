"""Tests for forum-topic tracking: crud upsert + stored topic titles."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.db import crud
from bot.db.models import Base, Chat, ChatTopic


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
# crud.record_topic_seen / set_topic_title / list_topics
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


async def test_record_topic_seen_stores_title(db_session):
    topic = await crud.record_topic_seen(db_session, -1001, 42, title="Обсуждения")
    assert topic.title == "Обсуждения"


async def test_record_topic_seen_none_title_keeps_existing(db_session):
    await crud.record_topic_seen(db_session, -1001, 42, title="Обсуждения")
    topic = await crud.record_topic_seen(db_session, -1001, 42)
    assert topic.title == "Обсуждения"  # a plain message never clears the name
    assert topic.message_count == 2


async def test_record_topic_seen_truncates_long_title(db_session):
    topic = await crud.record_topic_seen(db_session, -1001, 42, title="x" * 300)
    assert len(topic.title) == 128


async def test_set_topic_title_updates_without_counting(db_session):
    await crud.record_topic_seen(db_session, -1001, 42, title="Old")
    topic = await crud.set_topic_title(db_session, -1001, 42, "New")
    assert topic.title == "New"
    assert topic.message_count == 1  # a rename is not user activity


async def test_set_topic_title_creates_missing_row(db_session):
    topic = await crud.set_topic_title(db_session, -1001, 77, "Fresh")
    assert topic.thread_id == 77
    assert topic.title == "Fresh"
    assert await _row_count(db_session) == 1


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
                title="Topic two",
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
    assert topics[0].title == "Topic two"
    assert topics[1].title is None


async def test_list_topics_empty(db_session):
    assert await crud.list_topics(db_session, -1001) == []
