"""Handler tests for scheduled-posting commands and the worker tick."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot import scheduler as worker
from bot.db import crud
from bot.handlers import schedule
from tests.conftest import Cmd, make_message, make_user


@pytest.fixture
def patch_crud(monkeypatch):
    monkeypatch.setattr(crud, "count_scheduled_posts", AsyncMock(return_value=0))
    monkeypatch.setattr(
        crud, "add_scheduled_post", AsyncMock(return_value=SimpleNamespace(id=1))
    )
    monkeypatch.setattr(crud, "remove_scheduled_post", AsyncMock(return_value=True))
    monkeypatch.setattr(crud, "list_scheduled_posts", AsyncMock(return_value=[]))


# --------------------------------------------------------------------------- #
# /schedule
# --------------------------------------------------------------------------- #
async def test_schedule_once(base_data, patch_crud):
    msg = make_message(from_user=make_user(9, "Adm"))
    await schedule.cmd_schedule(msg, Cmd("1h | Hello"), **base_data)
    args, _ = crud.add_scheduled_post.await_args
    # (session, chat_id, body, run_at, interval, created_by)
    assert args[2] == "Hello"
    assert args[4] is None  # one-off
    assert args[5] == 9


async def test_schedule_recurring(base_data, patch_crud):
    msg = make_message(from_user=make_user(9, "Adm"))
    await schedule.cmd_schedule(msg, Cmd("10m 1h | Hi"), **base_data)
    args, _ = crud.add_scheduled_post.await_args
    assert args[4] == 3600


async def test_schedule_missing_separator(base_data, patch_crud):
    msg = make_message()
    await schedule.cmd_schedule(msg, Cmd("1h Hello"), **base_data)
    crud.add_scheduled_post.assert_not_awaited()


async def test_schedule_bad_delay(base_data, patch_crud):
    msg = make_message()
    await schedule.cmd_schedule(msg, Cmd("xx | Hello"), **base_data)
    crud.add_scheduled_post.assert_not_awaited()


async def test_schedule_interval_too_small(base_data, patch_crud):
    msg = make_message()
    await schedule.cmd_schedule(msg, Cmd("1h 1m | Hi"), **base_data)  # 60s < 300s
    crud.add_scheduled_post.assert_not_awaited()


async def test_schedule_non_admin(base_data, patch_crud):
    base_data["is_admin"] = False
    msg = make_message()
    await schedule.cmd_schedule(msg, Cmd("1h | Hi"), **base_data)
    crud.add_scheduled_post.assert_not_awaited()


# --------------------------------------------------------------------------- #
# /schedules and /unschedule
# --------------------------------------------------------------------------- #
async def test_schedules_empty(base_data, patch_crud):
    msg = make_message()
    await schedule.cmd_schedules(msg, Cmd(None), **base_data)
    msg.reply.assert_awaited_once()


async def test_schedules_list(base_data, monkeypatch):
    post = SimpleNamespace(
        id=3,
        run_at=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        interval_seconds=3600,
        text="Hello everyone, a long announcement body here",
    )
    monkeypatch.setattr(crud, "list_scheduled_posts", AsyncMock(return_value=[post]))
    msg = make_message()
    await schedule.cmd_schedules(msg, Cmd(None), **base_data)
    msg.reply.assert_awaited_once()


async def test_unschedule_ok(base_data, patch_crud):
    msg = make_message()
    await schedule.cmd_unschedule(msg, Cmd("5"), **base_data)
    crud.remove_scheduled_post.assert_awaited_once()


async def test_unschedule_bad_id(base_data, patch_crud):
    msg = make_message()
    await schedule.cmd_unschedule(msg, Cmd("abc"), **base_data)
    crud.remove_scheduled_post.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Worker tick
# --------------------------------------------------------------------------- #
class _FakeSessionCM:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


async def test_worker_tick_posts_and_advances(monkeypatch):
    post = SimpleNamespace(
        id=1,
        chat_id=-100,
        text="scheduled hello",
        run_at=datetime(2026, 7, 24, 11, 0, tzinfo=UTC),
        interval_seconds=3600,
    )
    monkeypatch.setattr(
        worker.crud, "due_scheduled_posts", AsyncMock(return_value=[post])
    )
    monkeypatch.setattr(worker.crud, "mark_post_ran", AsyncMock())
    monkeypatch.setattr(worker, "safe_send_message", AsyncMock(return_value=None))

    session = AsyncMock()
    session_maker = lambda: _FakeSessionCM(session)  # noqa: E731

    await worker._tick(bot=AsyncMock(), session_maker=session_maker)

    worker.safe_send_message.assert_awaited_once()
    worker.crud.mark_post_ran.assert_awaited_once()
    session.commit.assert_awaited_once()


async def test_worker_tick_escapes_post_text(monkeypatch):
    # Глобальный parse_mode="HTML": пользовательский текст поста экранируется
    # при отправке, иначе <b>/<i> из тела поста сломали бы сообщение.
    post = SimpleNamespace(
        id=1,
        chat_id=-100,
        text="Hello <b>bold</b> & <i>italic</i>",
        run_at=datetime(2026, 7, 24, 11, 0, tzinfo=UTC),
        interval_seconds=None,
    )
    monkeypatch.setattr(
        worker.crud, "due_scheduled_posts", AsyncMock(return_value=[post])
    )
    monkeypatch.setattr(worker.crud, "mark_post_ran", AsyncMock())
    sent = AsyncMock(return_value=None)
    monkeypatch.setattr(worker, "safe_send_message", sent)

    session = AsyncMock()
    session_maker = lambda: _FakeSessionCM(session)  # noqa: E731

    await worker._tick(bot=AsyncMock(), session_maker=session_maker)

    args, kwargs = sent.await_args
    assert args[2] == "Hello &lt;b&gt;bold&lt;/b&gt; &amp; &lt;i&gt;italic&lt;/i&gt;"
    assert kwargs.get("parse_mode") == "HTML"
