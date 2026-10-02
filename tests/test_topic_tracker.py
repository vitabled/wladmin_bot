"""Tests for ``TopicTrackerMiddleware`` (forum-topic bookkeeping).

Telegram's Bot API has no way to enumerate a forum's topics, so the bot can
only learn them from the updates it receives. The middleware must record:

* any group message carrying a ``message_thread_id``;
* ``forum_topic_created`` (with the topic NAME);
* ``forum_topic_edited`` (rename → update the stored title);

and it must do so in its own committed session, swallowing errors so the
update pipeline never breaks.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.db import crud
from bot.db.models import Base, Chat, ChatTopic
from bot.middlewares.topic_tracker import TopicTrackerMiddleware
from tests.conftest import make_chat


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        session.add(Chat(chat_id=-1001, title="Test", type="supergroup", language="ru"))
        await session.commit()
    yield session_maker
    await engine.dispose()


def _msg(**kwargs) -> MagicMock:
    msg = MagicMock()
    msg.chat = make_chat(-1001, "supergroup")
    msg.message_id = 555
    msg.message_thread_id = None
    msg.forum_topic_created = None
    msg.forum_topic_edited = None
    for key, value in kwargs.items():
        setattr(msg, key, value)
    return msg


async def _run(session_maker, message) -> object:
    mw = TopicTrackerMiddleware(session_maker)
    handler = AsyncMock(return_value="ok")
    result = await mw(handler, message, {})
    handler.assert_awaited_once()
    return result


async def _topics(session_maker) -> list[ChatTopic]:
    async with session_maker() as session:
        result = await session.execute(select(ChatTopic).order_by(ChatTopic.thread_id))
        return list(result.scalars().all())


async def test_plain_topic_message_is_recorded(maker):
    await _run(maker, _msg(message_thread_id=42))
    rows = await _topics(maker)
    assert [(t.thread_id, t.title) for t in rows] == [(42, None)]
    assert rows[0].message_count == 1


async def test_forum_topic_created_records_name(maker):
    created = SimpleNamespace(name="Обсуждения")
    await _run(maker, _msg(message_thread_id=42, forum_topic_created=created))
    rows = await _topics(maker)
    assert rows[0].thread_id == 42
    assert rows[0].title == "Обсуждения"


async def test_forum_topic_created_falls_back_to_message_id(maker):
    created = SimpleNamespace(name="Ideas")
    await _run(
        maker,
        _msg(message_thread_id=None, message_id=77, forum_topic_created=created),
    )
    rows = await _topics(maker)
    assert [(t.thread_id, t.title) for t in rows] == [(77, "Ideas")]


async def test_forum_topic_edited_updates_title(maker):
    await _run(
        maker,
        _msg(message_thread_id=42, forum_topic_created=SimpleNamespace(name="Old")),
    )
    await _run(
        maker,
        _msg(message_thread_id=42, forum_topic_edited=SimpleNamespace(name="New")),
    )
    rows = await _topics(maker)
    assert len(rows) == 1
    assert rows[0].title == "New"
    # A rename is not user activity.
    assert rows[0].message_count == 1


async def test_non_group_chat_is_ignored(maker):
    await _run(maker, _msg(chat=make_chat(111, "private"), message_thread_id=42))
    assert await _topics(maker) == []


async def test_message_without_thread_is_ignored(maker):
    await _run(maker, _msg(message_thread_id=None))
    assert await _topics(maker) == []


async def test_db_error_is_swallowed_and_handler_still_runs(maker, monkeypatch):
    monkeypatch.setattr(
        crud, "record_topic_seen", AsyncMock(side_effect=Exception("db down"))
    )
    result = await _run(maker, _msg(message_thread_id=42))
    assert result == "ok"


async def test_record_is_committed_even_if_handler_raises(maker):
    mw = TopicTrackerMiddleware(maker)

    async def boom(event, data):
        raise RuntimeError("handler failed")

    with pytest.raises(RuntimeError):
        await mw(boom, _msg(message_thread_id=99), {})

    rows = await _topics(maker)
    assert [t.thread_id for t in rows] == [99]
