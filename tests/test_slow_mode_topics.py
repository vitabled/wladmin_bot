"""Tests for per-topic slow-mode settings (``SlowModeTopic``).

Three layers:

* **crud** — get/list/set/clear of the per-topic override row, including the
  NULL-means-inherit contract and the ``UNSET`` sentinel that leaves columns
  alone on a partial update;
* **enforcement** (``bot/services/slow_mode.py``) — an override outranks both
  the chat intervals and the chat topic scope: «выкл здесь» exempts the topic,
  its own single interval applies to EVERYONE in it (sellers included), NULL
  inherits the chat's split, and a chat-wide «выкл» still wins;
* **DM screens** — every topic row in the list carries a «⚙️» button, the
  per-topic screen renders the four states, the switch/reset write through,
  the interval is picked with buttons (no typing) and the typed fallback
  parses one number / ``вкл`` / ``выкл`` / ``сброс`` (keeping the FSM state on
  bad input).
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.constants import SCAM_SOURCE_VERIFIED
from bot.db import crud
from bot.db.models import Base, Chat
from bot.handlers import dm_menu
from bot.services.slow_mode import check_and_record
from tests.conftest import make_bot, make_callback, make_chat, make_message, make_user

GROUP_CHAT_ID = 12345
CHAT_ID = -1001234
USER_ID = 1000


def _dm_chat():
    return make_chat(111, "private", "PM")


# --------------------------------------------------------------------------- #
# DB-backed crud tests
# --------------------------------------------------------------------------- #
@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add(Chat(chat_id=-1001, title="Test", type="supergroup", language="ru"))
        await session.commit()
        yield session
    await engine.dispose()


@pytest.fixture
def plain_crud(monkeypatch):
    """DB-backed tests: drop the crud mocks installed by the autouse fixture.

    ``patch_crud`` mocks every slow-mode crud call for the UI tests; these
    tests exercise the real implementations against SQLite instead.
    """
    monkeypatch.undo()
    return crud


async def test_get_slow_mode_topic_none_when_absent(db_session):
    assert await crud.get_slow_mode_topic(db_session, -1001, 42) is None


async def test_list_slow_mode_topics_empty_by_default(db_session):
    assert await crud.list_slow_mode_topics(db_session, -1001) == {}


async def test_set_slow_mode_topic_creates_enabled_row(db_session, plain_crud):
    row = await crud.set_slow_mode_topic(db_session, -1001, 42)
    assert row.chat_id == -1001
    assert row.thread_id == 42
    assert row.enabled is True  # new override defaults to on
    assert row.regular_seconds is None  # NULL = inherit the chat interval
    assert row.wl_seconds is None


async def test_set_slow_mode_topic_stores_own_intervals(db_session, plain_crud):
    await crud.set_slow_mode_topic(
        db_session, -1001, 42, enabled=True, regular_seconds=7200, wl_seconds=1800
    )
    fetched = await crud.get_slow_mode_topic(db_session, -1001, 42)
    assert (fetched.regular_seconds, fetched.wl_seconds) == (7200, 1800)


async def test_set_slow_mode_topic_unset_keeps_columns(db_session, plain_crud):
    await crud.set_slow_mode_topic(
        db_session, -1001, 42, enabled=True, regular_seconds=7200, wl_seconds=1800
    )
    await crud.set_slow_mode_topic(db_session, -1001, 42, enabled=False)
    fetched = await crud.get_slow_mode_topic(db_session, -1001, 42)
    assert fetched.enabled is False
    # UNSET (the default) must not wipe the stored intervals.
    assert (fetched.regular_seconds, fetched.wl_seconds) == (7200, 1800)


async def test_set_slow_mode_topic_none_means_inherit_again(db_session, plain_crud):
    await crud.set_slow_mode_topic(db_session, -1001, 42, regular_seconds=7200)
    await crud.set_slow_mode_topic(db_session, -1001, 42, regular_seconds=None)
    fetched = await crud.get_slow_mode_topic(db_session, -1001, 42)
    assert fetched.regular_seconds is None


async def test_list_slow_mode_topics_keys_by_thread(db_session, plain_crud):
    await crud.set_slow_mode_topic(db_session, -1001, 7, enabled=False)
    await crud.set_slow_mode_topic(db_session, -1001, 3, regular_seconds=60)
    await crud.set_slow_mode_topic(db_session, -1002, 9, enabled=False)
    rows = await crud.list_slow_mode_topics(db_session, -1001)
    assert sorted(rows) == [3, 7]
    assert rows[7].enabled is False


async def test_clear_slow_mode_topic_removes_row(db_session, plain_crud):
    await crud.set_slow_mode_topic(db_session, -1001, 42, enabled=False)
    assert await crud.clear_slow_mode_topic(db_session, -1001, 42) is True
    assert await crud.get_slow_mode_topic(db_session, -1001, 42) is None
    assert await crud.clear_slow_mode_topic(db_session, -1001, 42) is False


# --------------------------------------------------------------------------- #
# Enforcement: override vs chat config vs topic scope
# --------------------------------------------------------------------------- #
def _config(enabled=True, regular=21600, wl=10800, topic_ids=None) -> SimpleNamespace:
    return SimpleNamespace(
        enabled=enabled, regular_seconds=regular, wl_seconds=wl, topic_ids=topic_ids
    )


def _override(enabled=True, regular=None, wl=None, **punish) -> SimpleNamespace:
    """A SlowModeTopic-shaped row: interval override + its own punishment."""
    return SimpleNamespace(
        enabled=enabled,
        regular_seconds=regular,
        wl_seconds=wl,
        punish_text=punish.get("text"),
        punish_limit=punish.get("limit", 0),
        punish_action=punish.get("action", "mute"),
        punish_duration=punish.get("duration", 3600),
    )


def _patch_crud(monkeypatch, config, topic_override=None, scam_entry=None):
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=config))
    monkeypatch.setattr(
        crud, "get_slow_mode_topic", AsyncMock(return_value=topic_override)
    )
    monkeypatch.setattr(crud, "get_scam_entry", AsyncMock(return_value=scam_entry))


def _group_message(user_id=USER_ID, chat_id=CHAT_ID, topic: int = 0):
    msg = make_message(
        chat=make_chat(chat_id=chat_id, chat_type="supergroup"),
        from_user=make_user(user_id, is_bot=False),
    )
    msg.message_thread_id = topic  # MagicMock auto-attr is truthy; pin an int
    return msg


def _data(base_data, *, is_admin=False, is_owner=False) -> dict:
    return {**base_data, "is_admin": is_admin, "is_owner": is_owner}


async def test_override_off_exempts_scoped_topic(monkeypatch, base_data):
    _patch_crud(
        monkeypatch,
        _config(regular=60, wl=30, topic_ids=[3, 6]),
        topic_override=_override(enabled=False),
    )
    redis = base_data["redis"]
    msg = _group_message(topic=3)
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True
    redis.get.assert_not_called()
    redis.set.assert_not_called()  # off here: allowed and NOT recorded


async def test_override_off_exempts_topic_in_whole_chat_scope(monkeypatch, base_data):
    _patch_crud(
        monkeypatch, _config(regular=60, wl=30), topic_override=_override(enabled=False)
    )
    redis = base_data["redis"]
    msg = _group_message(topic=42)
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True
    redis.set.assert_not_called()


async def test_override_own_interval_is_used(monkeypatch, base_data):
    _patch_crud(
        monkeypatch,
        _config(regular=21600, wl=10800),
        topic_override=_override(enabled=True, regular=30, wl=15),
    )
    redis = base_data["redis"]
    redis.get.return_value = None
    msg = _group_message(topic=42)
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True
    args, kwargs = redis.set.await_args
    assert args[0] == f"slow:{CHAT_ID}:42:{USER_ID}"
    assert kwargs["ttl"] == 30 + 60  # the topic's own interval, not the chat's


async def test_override_own_interval_covers_sellers_too(monkeypatch, base_data):
    """A topic has ONE limit: the row's own value beats the chat's WL window.

    The legacy ``wl_seconds`` on the row (15) must NOT leak in — sellers get the
    same interval as everyone else in that topic (30).
    """
    _patch_crud(
        monkeypatch,
        _config(regular=21600, wl=10800),
        topic_override=_override(enabled=True, regular=30, wl=15),
        scam_entry=SimpleNamespace(source=SCAM_SOURCE_VERIFIED),
    )
    redis = base_data["redis"]
    redis.get.return_value = None
    msg = _group_message(topic=42)
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True
    _args, kwargs = redis.set.await_args
    assert kwargs["ttl"] == 30 + 60  # everyone, sellers included


async def test_override_null_intervals_inherit_chat(monkeypatch, base_data):
    _patch_crud(
        monkeypatch,
        _config(regular=60, wl=30),
        topic_override=_override(enabled=True),
    )
    redis = base_data["redis"]
    redis.get.return_value = None
    msg = _group_message(topic=42)
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True
    _args, kwargs = redis.set.await_args
    assert kwargs["ttl"] == 60 + 60


async def test_override_enables_topic_outside_chat_scope(monkeypatch, base_data):
    _patch_crud(
        monkeypatch,
        _config(regular=60, wl=30, topic_ids=[3]),
        topic_override=_override(enabled=True),
    )
    redis = base_data["redis"]
    redis.get.return_value = None
    msg = _group_message(topic=45)  # 45 is NOT in topic_ids
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True
    redis.set.assert_awaited_once()
    args, _kwargs = redis.set.await_args
    assert args[0] == f"slow:{CHAT_ID}:45:{USER_ID}"


async def test_topic_row_applies_even_with_the_chat_rule_off(monkeypatch, base_data):
    """A topic's own limit is self-contained: the chat switch does not gate it."""
    _patch_crud(
        monkeypatch,
        _config(enabled=False, regular=60, wl=30),
        topic_override=_override(enabled=True, regular=30, wl=15),
    )
    redis = base_data["redis"]
    redis.get.return_value = None
    msg = _group_message(topic=42)
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True
    _args, kwargs = redis.set.await_args
    assert kwargs["ttl"] == 30 + 60  # the topic's own value, not the chat's


async def test_topic_row_applies_without_any_chat_row(monkeypatch, base_data):
    """No chat config at all: a topic with its own value still limits."""
    _patch_crud(
        monkeypatch,
        None,
        topic_override=_override(enabled=True, regular=90, wl=None),
    )
    redis = base_data["redis"]
    redis.get.return_value = None
    msg = _group_message(topic=42)
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True
    _args, kwargs = redis.set.await_args
    assert kwargs["ttl"] == 90 + 60


async def test_topic_on_without_a_chat_row_uses_defaults(monkeypatch, base_data):
    """«Включить здесь» with no chat row falls back to the 6 h / 3 h defaults."""
    _patch_crud(monkeypatch, None, topic_override=_override(enabled=True))
    redis = base_data["redis"]
    redis.get.return_value = None
    msg = _group_message(topic=42)
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True
    _args, kwargs = redis.set.await_args
    assert kwargs["ttl"] == 21600 + 60


async def test_chat_rule_off_still_leaves_other_topics_free(monkeypatch, base_data):
    """Topics without a row keep following the chat switch (no limit when off)."""
    _patch_crud(monkeypatch, _config(enabled=False, regular=60, wl=30))
    redis = base_data["redis"]
    msg = _group_message(topic=45)
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True
    redis.get.assert_not_called()
    redis.set.assert_not_called()


async def test_non_forum_message_ignores_overrides(monkeypatch, base_data):
    _patch_crud(
        monkeypatch,
        _config(regular=60, wl=30, topic_ids=[3]),
        topic_override=_override(enabled=True),
    )
    redis = base_data["redis"]
    msg = _group_message(topic=0)  # general/no-topic messages are never overridden
    assert await check_and_record(make_bot(), msg, _data(base_data)) is True
    redis.get.assert_not_called()


# --------------------------------------------------------------------------- #
# DM screens
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def patch_crud(monkeypatch):
    """No DB in the UI tests: every slow-mode crud call is mocked by default."""
    for name in ("list_topics", "get_slow_mode", "set_slow_mode", "get_chat"):
        monkeypatch.setattr(crud, name, AsyncMock())
    monkeypatch.setattr(crud, "list_topics", AsyncMock(return_value=[]))
    monkeypatch.setattr(crud, "list_slow_mode_topics", AsyncMock(return_value={}))
    monkeypatch.setattr(crud, "get_slow_mode_topic", AsyncMock(return_value=None))
    monkeypatch.setattr(crud, "set_slow_mode_topic", AsyncMock())
    monkeypatch.setattr(crud, "clear_slow_mode_topic", AsyncMock(return_value=True))


@pytest.fixture
async def fsm():
    storage = MemoryStorage()
    ctx = FSMContext(
        storage=storage, key=StorageKey(bot_id=42, chat_id=111, user_id=1000)
    )
    yield ctx
    await storage.close()


def _cb(data: str):
    cb = make_callback(
        data=data, from_user=make_user(1000, "Actor", "actor"), chat=_dm_chat()
    )
    cb.message.edit_text = AsyncMock()
    cb.message.answer = AsyncMock()
    cb.message.edit_reply_markup = AsyncMock()
    return cb


def _topic(thread_id: int, count: int = 1, title: str | None = None):
    return SimpleNamespace(thread_id=thread_id, message_count=count, title=title)


def _capturing_translator(base_data):
    """Replace ``_`` with one recording (key, kwargs) — assert on states too."""
    calls: list[tuple[str, dict]] = []

    def t(key, **kwargs):
        calls.append((key, kwargs))
        return key

    base_data["_"] = t
    return calls


def _kb_rows(cb) -> list[list[str]]:
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    return [[btn.callback_data for btn in row] for row in kb.inline_keyboard]


def _all_callbacks(cb) -> list[str]:
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    return [btn.callback_data for row in kb.inline_keyboard for btn in row]


async def test_topics_list_has_params_button_per_topic(base_data, fsm, monkeypatch):
    monkeypatch.setattr(
        crud, "get_slow_mode", AsyncMock(return_value=_config(topic_ids=[3]))
    )
    monkeypatch.setattr(
        crud,
        "list_topics",
        AsyncMock(
            return_value=[_topic(3, count=10, title="Новости"), _topic(6, count=4)]
        ),
    )
    monkeypatch.setattr(
        crud,
        "list_slow_mode_topics",
        AsyncMock(return_value={6: _override(enabled=False)}),
    )

    cb = _cb(f"dm:smtl:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert cb.message.edit_text.await_args.args[0] == "dm_sm_topics_prompt"
    assert _kb_rows(cb) == [
        [f"dm:smc:{GROUP_CHAT_ID}"],  # the chat-wide rule itself, one tap
        [f"dm:smb:{GROUP_CHAT_ID}:3", f"dm:smt:{GROUP_CHAT_ID}:3"],
        [f"dm:smb:{GROUP_CHAT_ID}:6", f"dm:smt:{GROUP_CHAT_ID}:6"],
        [f"dm:smball:{GROUP_CHAT_ID}"],
        [f"dm:smbdone:{GROUP_CHAT_ID}"],
        [f"dm:smrefresh:{GROUP_CHAT_ID}"],
        [f"dm:smadd:{GROUP_CHAT_ID}"],
        [f"dm:g:{GROUP_CHAT_ID}", "dm:menu"],
    ]
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].text == "dm_sm_chat_rule_on"  # 6 ч / 3 ч
    assert kb.inline_keyboard[1][0].text == "✅ Новости · 10 сообщ."
    assert kb.inline_keyboard[1][1].text == "dm_sm_topic_params"
    assert kb.inline_keyboard[1][1].icon_custom_emoji_id == "5877260593903177342"
    # Overrides show up on the row (the test translator echoes the key):
    # «· dm_sm_topic_status_off» = the rule is off in that topic.
    assert kb.inline_keyboard[2][0].text == "☑️ #6 · 4 сообщ. · dm_sm_topic_status_off"
    # The list behaves like the post-«вкл» step, so the FSM is seeded from the
    # chat-level config («Все ветки» / «Готово» keep working).
    assert await fsm.get_state() == dm_menu.DmSlowMode.awaiting_topics
    state_data = await fsm.get_data()
    assert state_data["selected_topics"] == [3]
    assert state_data["pending_sm"] == {
        "enabled": True,
        "regular": 21600,
        "wl": 10800,
    }
    cb.answer.assert_awaited_once()


async def test_topics_list_own_params_mark(base_data, fsm, monkeypatch):
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    monkeypatch.setattr(
        crud,
        "list_topics",
        AsyncMock(return_value=[_topic(3, count=2, title="Новости")]),
    )
    monkeypatch.setattr(
        crud,
        "list_slow_mode_topics",
        AsyncMock(return_value={3: _override(enabled=True, regular=7200)}),
    )

    cb = _cb(f"dm:smtl:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert (
        kb.inline_keyboard[1][0].text
        == "☑️ Новости · 2 сообщ. · dm_sm_topic_status_own"
    )


async def test_topics_list_empty_shows_hint_screen(base_data, fsm, monkeypatch):
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=None))
    monkeypatch.setattr(crud, "list_topics", AsyncMock(return_value=[]))

    cb = _cb(f"dm:smtl:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert cb.message.edit_text.await_args.args[0] == "dm_sm_topics_empty"
    assert _all_callbacks(cb) == [
        f"dm:smc:{GROUP_CHAT_ID}",  # the chat-wide rule is reachable here too
        f"dm:smball:{GROUP_CHAT_ID}",
        f"dm:smadd:{GROUP_CHAT_ID}",
        f"dm:smback:{GROUP_CHAT_ID}",
    ]


async def test_topic_gear_opens_the_grid_directly(base_data, fsm, monkeypatch):
    """«⚙️» in the topic list lands on the hour grid — no intermediate screen."""
    calls = _capturing_translator(base_data)
    monkeypatch.setattr(
        crud, "get_slow_mode", AsyncMock(return_value=_config(regular=21600, wl=10800))
    )
    monkeypatch.setattr(
        crud, "list_topics", AsyncMock(return_value=[_topic(3, title="Новости")])
    )

    cb = _cb(f"dm:smt:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert "dm_sm_topic_pick_all" in cb.message.edit_text.await_args.args[0]
    assert next(kw for key, kw in calls if key == "dm_sm_topic_pick_all") == {
        "topic": "Новости"
    }
    assert next(kw for key, kw in calls if key == "dm_sm_topic_state_inherit") == {
        "regular": 6,
        "wl": 3,
    }
    data = _all_callbacks(cb)
    # the grid itself: presets, ±1 h (6 h is the current value), manual, nav
    assert f"dm:smtvs:{GROUP_CHAT_ID}:3:6" in data
    assert f"dm:smtvp:{GROUP_CHAT_ID}:3" in data
    assert f"dm:smtl:{GROUP_CHAT_ID}" in data
    # …and nothing of the removed intermediate screen
    assert f"dm:smtv:{GROUP_CHAT_ID}:3" not in data
    assert f"dm:smtr:{GROUP_CHAT_ID}:3" not in data  # no override yet
    # «⛔ Выключить здесь» is gone: a topic either has its own limit or follows
    # the chat, and the rule is switched at the chat level only.
    assert not [item for item in data if ":smt" in item and item[-2:] in (":o", ":c")]
    assert not [item for item in data if ":smtx:" in item]


async def test_topic_grid_marks_its_own_value(base_data, fsm, monkeypatch):
    """The grid of a topic with its own limit marks it and offers the reset."""
    calls = _capturing_translator(base_data)
    monkeypatch.setattr(
        crud, "get_slow_mode", AsyncMock(return_value=_config(regular=21600, wl=10800))
    )
    monkeypatch.setattr(
        crud,
        "get_slow_mode_topic",
        AsyncMock(return_value=_override(enabled=True, regular=7200)),
    )
    monkeypatch.setattr(crud, "list_topics", AsyncMock(return_value=[]))

    cb = _cb(f"dm:smt:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    own = next(kw for key, kw in calls if key == "dm_sm_topic_state_own")
    assert own == {"hours": "dm_sm_hours"}  # label of the topic's own 2h value
    assert ("dm_sm_hours", {"hours": 2}) in calls  # …and that value is 2h
    assert f"dm:smtvi:{GROUP_CHAT_ID}:3" in _all_callbacks(cb)  # «↩️ Как в чате»


async def test_topic_grid_never_offers_off_here(base_data, fsm, monkeypatch):
    """«⛔ Выключить здесь» is gone from the grid — in every state.

    A legacy row with ``enabled=False`` is still explained in the screen text
    (the database may hold one), but there is no button to create or flip it.
    """
    calls = _capturing_translator(base_data)
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    monkeypatch.setattr(
        crud, "get_slow_mode_topic", AsyncMock(return_value=_override(enabled=False))
    )

    cb = _cb(f"dm:smt:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert any(key == "dm_sm_topic_state_off" for key, _kw in calls)
    data = _all_callbacks(cb)
    assert f"dm:smtvo:{GROUP_CHAT_ID}:3" not in data
    assert f"dm:smtvc:{GROUP_CHAT_ID}:3" not in data

    # No row of its own + the chat rule off → an informational line, still no
    # off/on button.
    calls.clear()
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=None))
    monkeypatch.setattr(crud, "get_slow_mode_topic", AsyncMock(return_value=None))
    cb = _cb(f"dm:smt:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert any(key == "dm_sm_topic_state_chat_off" for key, _kw in calls)
    assert not [
        item
        for item in _all_callbacks(cb)
        if item.startswith(f"dm:smtvo:{GROUP_CHAT_ID}")
    ]
    assert not [
        item
        for item in _all_callbacks(cb)
        if item.startswith(f"dm:smtvc:{GROUP_CHAT_ID}")
    ]


async def test_removed_switch_callback_is_inert(base_data, fsm, monkeypatch):
    """A press on the deleted «выключить здесь» button changes nothing.

    Old messages still carry ``dm:smtx:``/``dm:smtvo:``; the handler is gone, so
    the press must not write anything (it only gets acknowledged).
    """
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    monkeypatch.setattr(crud, "get_slow_mode_topic", AsyncMock(return_value=None))
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)

    for legacy in (f"dm:smtx:{GROUP_CHAT_ID}:3", f"dm:smtvo:{GROUP_CHAT_ID}:3"):
        cb = _cb(legacy)
        await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
        cb.answer.assert_awaited_once()
        cb.message.edit_text.assert_not_awaited()

    set_mock.assert_not_awaited()


async def test_topic_reset_clears_override(base_data, fsm, monkeypatch):
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    clear_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(crud, "clear_slow_mode_topic", clear_mock)

    cb = _cb(f"dm:smtr:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    clear_mock.assert_awaited_once_with(base_data["session"], GROUP_CHAT_ID, 3)
    base_data["session"].commit.assert_awaited_once()
    assert "dm_sm_topic_pick_all" in cb.message.edit_text.await_args.args[0]


async def test_topic_set_asks_for_intervals(base_data, fsm, monkeypatch):
    monkeypatch.setattr(
        crud, "list_topics", AsyncMock(return_value=[_topic(3, title="Новости")])
    )

    cb = _cb(f"dm:smtp:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert await fsm.get_state() == dm_menu.DmSlowMode.awaiting_topic_params
    assert (await fsm.get_data())["thread_id"] == 3
    assert cb.message.edit_text.await_args.args[0] == "dm_sm_topic_params_prompt"
    assert _kb_rows(cb) == [
        [f"dm:smtl:{GROUP_CHAT_ID}", "dm:menu"],
    ]


# --- per-topic intervals by buttons (no typing) ----------------------------- #


def _btn_texts(cb) -> list[str]:
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    return [btn.text for row in kb.inline_keyboard for btn in row]


async def test_topic_grid_uses_the_effective_hours(base_data, fsm, monkeypatch):
    """The grid's «сейчас» uses the topic's own value, not the chat's."""
    calls = _capturing_translator(base_data)
    monkeypatch.setattr(
        crud, "get_slow_mode", AsyncMock(return_value=_config(regular=21600, wl=10800))
    )
    monkeypatch.setattr(
        crud,
        "get_slow_mode_topic",
        AsyncMock(return_value=_override(enabled=True, regular=7200)),
    )

    cb = _cb(f"dm:smt:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert ("dm_sm_hours", {"hours": 2}) in calls  # own value wins
    assert next(kw for key, kw in calls if key == "dm_sm_topic_pick_now") == {
        "current": "dm_sm_hours"
    }
    # 2 h is not a preset, so no preset is ticked — the value lives in «сейчас»
    assert not [text for text in _btn_texts(cb) if text.startswith("✅ ")]
    assert f"dm:smtvm:{GROUP_CHAT_ID}:3" in _all_callbacks(cb)  # ±1 ч available


async def test_topic_pick_opens_grid_and_marks_current(base_data, fsm, monkeypatch):
    """«🕒» opens the hour grid; the matching preset carries «✅»."""
    calls = _capturing_translator(base_data)
    monkeypatch.setattr(
        crud, "get_slow_mode", AsyncMock(return_value=_config(regular=21600, wl=10800))
    )
    monkeypatch.setattr(
        crud, "list_topics", AsyncMock(return_value=[_topic(3, title="Новости")])
    )

    cb = _cb(f"dm:smtv:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert cb.message.edit_text.await_args.args[0].startswith("dm_sm_topic_pick_all")
    assert ("dm_sm_hours", {"hours": 6}) in calls
    assert next(kw for key, kw in calls if key == "dm_sm_topic_pick_now") == {
        "current": "dm_sm_hours"
    }
    assert _btn_texts(cb)[2] == "✅ dm_sm_hours"  # 6h = chat value → marked
    assert _kb_rows(cb) == [
        [
            f"dm:smtvs:{GROUP_CHAT_ID}:3:1",
            f"dm:smtvs:{GROUP_CHAT_ID}:3:3",
            f"dm:smtvs:{GROUP_CHAT_ID}:3:6",
        ],
        [
            f"dm:smtvs:{GROUP_CHAT_ID}:3:12",
            f"dm:smtvs:{GROUP_CHAT_ID}:3:24",
            f"dm:smtvs:{GROUP_CHAT_ID}:3:48",
        ],
        [f"dm:smtvs:{GROUP_CHAT_ID}:3:0"],
        [f"dm:smtvm:{GROUP_CHAT_ID}:3", f"dm:smtvp:{GROUP_CHAT_ID}:3"],
        [f"dm:smtp:{GROUP_CHAT_ID}:3"],
        [f"dm:smw:{GROUP_CHAT_ID}:3"],  # punishment of THIS topic
        [f"dm:smtl:{GROUP_CHAT_ID}", "dm:menu"],
    ]
    cb.answer.assert_awaited_once()


async def test_topic_pick_unlimited_hides_step_buttons(base_data, fsm, monkeypatch):
    """«∞ без лимита» is marked, and «±1 ч» disappears (nothing to step)."""
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    monkeypatch.setattr(
        crud,
        "get_slow_mode_topic",
        AsyncMock(return_value=_override(enabled=True, regular=0)),
    )

    cb = _cb(f"dm:smtv:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert f"dm:smtvm:{GROUP_CHAT_ID}:3" not in _all_callbacks(cb)
    assert f"dm:smtvp:{GROUP_CHAT_ID}:3" not in _all_callbacks(cb)
    assert "✅ dm_sm_topic_pick_unlimited" in _btn_texts(cb)


async def test_topic_pick_step_from_unlimited_pins_one_hour(
    base_data, fsm, monkeypatch
):
    """Even if «+1 ч» is pressed for «∞», the result stays a real value."""
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    monkeypatch.setattr(
        crud,
        "get_slow_mode_topic",
        AsyncMock(return_value=_override(enabled=True, regular=0)),
    )
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)

    cb = _cb(f"dm:smtvp:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    set_mock.assert_awaited_once_with(
        base_data["session"],
        GROUP_CHAT_ID,
        3,
        enabled=True,
        regular_seconds=3600,
        wl_seconds=None,
    )


async def test_topic_pick_set_pins_value_and_redraws(base_data, fsm, monkeypatch):
    """A preset press pins the topic's limit for everyone and redraws the grid."""
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)

    cb = _cb(f"dm:smtvs:{GROUP_CHAT_ID}:3:12")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    set_mock.assert_awaited_once_with(
        base_data["session"],
        GROUP_CHAT_ID,
        3,
        enabled=True,
        regular_seconds=43200,
        wl_seconds=None,
    )
    base_data["session"].commit.assert_awaited_once()
    assert cb.message.edit_text.await_args.args[0].startswith("dm_sm_topic_pick_all")
    cb.answer.assert_awaited_once()


async def test_topic_pick_zero_means_unlimited(base_data, fsm, monkeypatch):
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)

    cb = _cb(f"dm:smtvs:{GROUP_CHAT_ID}:3:0")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    set_mock.assert_awaited_once_with(
        base_data["session"],
        GROUP_CHAT_ID,
        3,
        enabled=True,
        regular_seconds=0,
        wl_seconds=None,
    )


async def test_topic_pick_rejects_out_of_range(base_data, fsm, monkeypatch):
    """721h is refused with an alert and nothing is written."""
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)

    cb = _cb(f"dm:smtvs:{GROUP_CHAT_ID}:3:721")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    set_mock.assert_not_awaited()
    cb.message.edit_text.assert_not_awaited()
    assert cb.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.parametrize("delta,expected", [(-1, 18000), (1, 25200)])
async def test_topic_pick_step_moves_the_single_value(
    base_data, fsm, monkeypatch, delta, expected
):
    """A legacy per-topic ``wl`` (3600) must not skew the one value (6h)."""
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    monkeypatch.setattr(
        crud,
        "get_slow_mode_topic",
        AsyncMock(return_value=_override(enabled=True, regular=21600, wl=3600)),
    )
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)

    action = "smtvm" if delta < 0 else "smtvp"
    cb = _cb(f"dm:{action}:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    set_mock.assert_awaited_once_with(
        base_data["session"],
        GROUP_CHAT_ID,
        3,
        enabled=True,
        regular_seconds=expected,
        wl_seconds=None,
    )


async def test_topic_pick_step_clamps_at_one_hour(base_data, fsm, monkeypatch):
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    monkeypatch.setattr(
        crud,
        "get_slow_mode_topic",
        AsyncMock(return_value=_override(enabled=True, regular=3600)),
    )
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)

    cb = _cb(f"dm:smtvm:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    set_mock.assert_awaited_once_with(
        base_data["session"],
        GROUP_CHAT_ID,
        3,
        enabled=True,
        regular_seconds=3600,
        wl_seconds=None,
    )


async def test_topic_pick_inherit_drops_the_row(base_data, fsm, monkeypatch):
    """«↩️ Как в чате» removes the row — nothing left to mark as «own params»."""
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    monkeypatch.setattr(
        crud,
        "get_slow_mode_topic",
        AsyncMock(return_value=_override(enabled=True, regular=7200, wl=3600)),
    )
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)
    clear_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(crud, "clear_slow_mode_topic", clear_mock)

    cb = _cb(f"dm:smtvi:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    clear_mock.assert_awaited_once_with(base_data["session"], GROUP_CHAT_ID, 3)
    set_mock.assert_not_awaited()
    base_data["session"].commit.assert_awaited_once()
    assert cb.message.edit_text.await_args.args[0].startswith("dm_sm_topic_pick_all")


async def test_topic_pick_inherit_keeps_off_here_row(base_data, fsm, monkeypatch):
    """A topic switched off here keeps its row: «как в чате» only clears limits."""
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    monkeypatch.setattr(
        crud,
        "get_slow_mode_topic",
        AsyncMock(return_value=_override(enabled=False, regular=7200)),
    )
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)
    clear_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(crud, "clear_slow_mode_topic", clear_mock)

    cb = _cb(f"dm:smtvi:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    set_mock.assert_awaited_once_with(
        base_data["session"], GROUP_CHAT_ID, 3, regular_seconds=None, wl_seconds=None
    )
    clear_mock.assert_not_awaited()


async def test_topic_pick_hides_inherit_without_own_value(base_data, fsm, monkeypatch):
    """While the topic has no limit of its own there is nothing to reset."""
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))

    cb = _cb(f"dm:smtv:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert f"dm:smtvi:{GROUP_CHAT_ID}:3" not in _all_callbacks(cb)
    # …но ручной ввод и возврат к списку веток остаются доступными.
    assert f"dm:smtp:{GROUP_CHAT_ID}:3" in _all_callbacks(cb)
    assert f"dm:smtl:{GROUP_CHAT_ID}" in _all_callbacks(cb)


async def test_topic_pick_ignores_legacy_wl_only_row(base_data, fsm, monkeypatch):
    """A row left over with only ``wl_seconds`` is not an «own value» anymore."""
    calls = _capturing_translator(base_data)
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    monkeypatch.setattr(
        crud,
        "get_slow_mode_topic",
        AsyncMock(return_value=_override(enabled=True, wl=3600)),
    )

    cb = _cb(f"dm:smtv:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    # Marked value = the chat's 6h, and no «↩️ Как в чате» row to reset it.
    assert ("dm_sm_hours", {"hours": 6}) in calls
    assert f"dm:smtvi:{GROUP_CHAT_ID}:3" not in _all_callbacks(cb)
    assert _btn_texts(cb)[2] == "✅ dm_sm_hours"


async def test_topic_pick_shows_inherit_with_own_value(base_data, fsm, monkeypatch):
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    monkeypatch.setattr(
        crud,
        "get_slow_mode_topic",
        AsyncMock(return_value=_override(enabled=True, regular=3600)),
    )

    cb = _cb(f"dm:smtv:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert f"dm:smtvi:{GROUP_CHAT_ID}:3" in _all_callbacks(cb)


async def test_topic_callbacks_accept_legacy_role_suffix(base_data, fsm, monkeypatch):
    """Old messages carry ``:r``/``:w`` in the data — those presses still work."""
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)
    clear_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(crud, "clear_slow_mode_topic", clear_mock)

    cb = _cb(f"dm:smtvs:{GROUP_CHAT_ID}:3:w:6")  # legacy preset press
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    set_mock.assert_awaited_once_with(
        base_data["session"],
        GROUP_CHAT_ID,
        3,
        enabled=True,
        regular_seconds=21600,
        wl_seconds=None,
    )

    set_mock.reset_mock()
    cb = _cb(f"dm:smtv:{GROUP_CHAT_ID}:3:r")  # opens the single-value grid
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert cb.message.edit_text.await_args.args[0].startswith("dm_sm_topic_pick_all")

    cb = _cb(f"dm:smtvi:{GROUP_CHAT_ID}:3:w")  # «как в чате» drops the row
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    clear_mock.assert_awaited_once_with(base_data["session"], GROUP_CHAT_ID, 3)


async def test_topic_callbacks_reject_malformed_data(base_data, fsm):
    for data in (
        f"dm:smt:{GROUP_CHAT_ID}",
        f"dm:smtx:{GROUP_CHAT_ID}:x",
        "dm:smtl:abc",
        f"dm:smtv:{GROUP_CHAT_ID}",
        f"dm:smtv:{GROUP_CHAT_ID}:3:x",
        f"dm:smtvs:{GROUP_CHAT_ID}:3:r",
        f"dm:smtvs:{GROUP_CHAT_ID}:3:abc",
        f"dm:smtvm:{GROUP_CHAT_ID}:3:x",
    ):
        cb = _cb(data)
        await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
        cb.message.edit_text.assert_not_awaited()
        cb.answer.assert_awaited_once()


# --- typed per-topic intervals --------------------------------------------- #


async def _await_params(fsm, thread_id: int = 3) -> None:
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_topic_params)
    await fsm.update_data(chat_id=GROUP_CHAT_ID, thread_id=thread_id)


async def test_params_one_number_sets_the_limit(base_data, fsm, monkeypatch):
    """«6» → one limit for everyone (legacy per-topic ``wl`` wiped) + enabled."""
    await _await_params(fsm)
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)

    msg = make_message(text="6", chat=_dm_chat())
    await dm_menu.dm_sm_topic_params(msg, state=fsm, **base_data)

    assert [call.kwargs for call in set_mock.await_args_list] == [
        {"enabled": True, "regular_seconds": 21600, "wl_seconds": None},
    ]
    base_data["session"].commit.assert_awaited_once_with()
    assert await fsm.get_state() is None
    assert "dm_sm_topic_pick_all" in msg.answer.await_args.args[0]


@pytest.mark.parametrize("text", ["вкл 6", "ВКЛ 6", "6", "6 ч", "6h"])
async def test_params_accepts_prefix_case_and_unit(base_data, fsm, monkeypatch, text):
    await _await_params(fsm)
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)

    msg = make_message(text=text, chat=_dm_chat())
    await dm_menu.dm_sm_topic_params(msg, state=fsm, **base_data)

    _args, kwargs = set_mock.await_args_list[0]
    assert kwargs == {"enabled": True, "regular_seconds": 21600, "wl_seconds": None}


@pytest.mark.parametrize("text", ["0", "без лимита", "∞"])
async def test_params_unlimited_words_mean_no_limit(base_data, fsm, monkeypatch, text):
    await _await_params(fsm)
    monkeypatch.setattr(
        crud, "get_slow_mode", AsyncMock(return_value=_config(regular=60, wl=30))
    )
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)

    msg = make_message(text=text, chat=_dm_chat())
    await dm_menu.dm_sm_topic_params(msg, state=fsm, **base_data)

    _args, kwargs = set_mock.await_args_list[0]
    assert kwargs == {"enabled": True, "regular_seconds": 0, "wl_seconds": None}


async def test_params_clamps_to_720_hours(base_data, fsm, monkeypatch):
    await _await_params(fsm)
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)

    msg = make_message(text="999", chat=_dm_chat())
    await dm_menu.dm_sm_topic_params(msg, state=fsm, **base_data)

    _args, kwargs = set_mock.await_args_list[0]
    assert kwargs["regular_seconds"] == 720 * 3600


async def test_params_bare_on_enables_with_chat_intervals(base_data, fsm, monkeypatch):
    await _await_params(fsm)
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)

    msg = make_message(text="вкл", chat=_dm_chat())
    await dm_menu.dm_sm_topic_params(msg, state=fsm, **base_data)

    set_mock.assert_awaited_once_with(
        base_data["session"],
        GROUP_CHAT_ID,
        3,
        enabled=True,
        regular_seconds=None,
        wl_seconds=None,
    )


async def test_params_off_disables_only_this_topic(base_data, fsm, monkeypatch):
    await _await_params(fsm)
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)

    msg = make_message(text="выкл", chat=_dm_chat())
    await dm_menu.dm_sm_topic_params(msg, state=fsm, **base_data)

    set_mock.assert_awaited_once_with(
        base_data["session"], GROUP_CHAT_ID, 3, enabled=False
    )
    assert await fsm.get_state() is None


async def test_params_reset_drops_override(base_data, fsm, monkeypatch):
    await _await_params(fsm)
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    clear_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(crud, "clear_slow_mode_topic", clear_mock)

    msg = make_message(text="сброс", chat=_dm_chat())
    await dm_menu.dm_sm_topic_params(msg, state=fsm, **base_data)

    clear_mock.assert_awaited_once_with(base_data["session"], GROUP_CHAT_ID, 3)
    base_data["session"].commit.assert_awaited_once()
    assert "dm_sm_topic_pick_all" in msg.answer.await_args.args[0]


@pytest.mark.parametrize("text", ["абракадабра", "6 3", "6 3 9", ""])
async def test_params_bad_input_keeps_state(base_data, fsm, monkeypatch, text):
    await _await_params(fsm)
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)
    clear_mock = AsyncMock()
    monkeypatch.setattr(crud, "clear_slow_mode_topic", clear_mock)

    msg = make_message(text=text, chat=_dm_chat())
    await dm_menu.dm_sm_topic_params(msg, state=fsm, **base_data)

    assert msg.answer.await_args.args[0] == "dm_sm_topic_params_bad"
    set_mock.assert_not_awaited()
    clear_mock.assert_not_awaited()
    assert await fsm.get_state() == dm_menu.DmSlowMode.awaiting_topic_params


async def test_params_without_state_data_returns_to_menu(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_topic_params)

    msg = make_message(text="6", chat=_dm_chat())
    await dm_menu.dm_sm_topic_params(msg, state=fsm, **base_data)

    assert msg.answer.await_args.args[0] == "dm_menu_title"
    assert await fsm.get_state() is None


# --------------------------------------------------------------------------- #
# Punishment for violations (slow_mode_topics.punish_*)
# --------------------------------------------------------------------------- #
def _violation_data(base_data) -> dict:
    """Pipeline data for a blocked message from an ordinary member."""
    return {**base_data, "is_admin": False, "is_owner": False}


def _blocked(monkeypatch, base_data, *, limit=3, incr=1, config=None, **punish):
    """Patch crud for a blocked message in a topic that carries a punishment."""
    override = _override(enabled=True, regular=60, limit=limit, **punish)
    _patch_crud(
        monkeypatch,
        config if config is not None else _config(regular=60, wl=30),
        topic_override=override,
    )
    redis = base_data["redis"]
    redis.get.return_value = str(int(time.time()) - 10)  # posted 10s ago
    redis.incr.return_value = incr
    return _violation_data(base_data)


def _kb_texts(cb) -> list[str]:
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    return [btn.text for btn in rows_flat(kb)]


def rows_flat(kb):
    """Inline buttons of a markup, flattened (row order kept)."""
    return [btn for row in kb.inline_keyboard for btn in row]


def _patch_screen(
    monkeypatch,
    base_data,
    row=None,
    *,
    topic="Задачи",
    thread_id=3,
    topics=None,
):
    """Patch crud for the topic's punishment screen and its writes."""
    setter = AsyncMock(return_value=row)
    monkeypatch.setattr(crud, "get_slow_mode_topic", AsyncMock(return_value=row))
    monkeypatch.setattr(crud, "set_slow_mode_topic", setter)
    monkeypatch.setattr(
        crud,
        "list_topics",
        AsyncMock(
            return_value=(
                [_topic(thread_id, title=topic)] if topics is None else topics
            )
        ),
    )
    return setter


def _topic_row(**punish):
    """A SlowModeTopic-shaped row holding this topic's punishment."""
    return _override(enabled=True, regular=3600, **punish)


# --- enforcement ----------------------------------------------------------- #


async def test_custom_warn_text_goes_to_the_violator(monkeypatch, base_data):
    data = _blocked(monkeypatch, base_data, text="🚫 Не так быстро!")
    bot, msg = make_bot(), _group_message(topic=3)
    assert await check_and_record(bot, msg, data) is False
    msg.reply.assert_awaited_once_with("🚫 Не так быстро!", parse_mode="HTML")


async def test_bad_html_in_the_warn_text_falls_back_to_plain(monkeypatch, base_data):
    """A tag Telegram rejects must not swallow the warning."""
    data = _blocked(monkeypatch, base_data, text="<b>oops")
    bot, msg = make_bot(), _group_message(topic=3)
    msg.reply.side_effect = [RuntimeError("bad entities"), None]
    assert await check_and_record(bot, msg, data) is False
    assert msg.reply.await_args_list[0].kwargs == {"parse_mode": "HTML"}
    assert msg.reply.await_args_list[1].args == ("<b>oops",)


async def test_first_violation_is_counted_with_the_window(monkeypatch, base_data):
    data = _blocked(monkeypatch, base_data, limit=3, incr=1)
    bot, msg = make_bot(), _group_message(topic=3)
    assert await check_and_record(bot, msg, data) is False
    redis = base_data["redis"]
    redis.incr.assert_awaited_once_with(f"slowviol:{CHAT_ID}:3:{USER_ID}")
    redis.expire.assert_awaited_once_with(f"slowviol:{CHAT_ID}:3:{USER_ID}", 60 + 60)
    bot.restrict_chat_member.assert_not_awaited()


async def test_violation_below_the_limit_punishes_nobody(monkeypatch, base_data):
    data = _blocked(monkeypatch, base_data, limit=3, incr=2)
    bot, msg = make_bot(), _group_message(topic=3)
    assert await check_and_record(bot, msg, data) is False
    base_data["redis"].expire.assert_not_awaited()  # TTL is set on the 1st only
    bot.restrict_chat_member.assert_not_awaited()
    bot.send_message.assert_not_awaited()


async def test_zero_limit_never_counts_a_violation(monkeypatch, base_data):
    data = _blocked(monkeypatch, base_data, limit=0, incr=99)
    bot, msg = make_bot(), _group_message(topic=3)
    assert await check_and_record(bot, msg, data) is False
    base_data["redis"].incr.assert_not_awaited()


async def test_topic_without_a_row_of_its_own_never_punishes(monkeypatch, base_data):
    """The chat-wide rule warns, but only a topic's own rule may punish."""
    _patch_crud(monkeypatch, _config(regular=60, wl=30), topic_override=None)
    redis = base_data["redis"]
    redis.get.return_value = str(int(time.time()) - 10)
    bot, msg = make_bot(), _group_message(topic=3)

    assert await check_and_record(bot, msg, _violation_data(base_data)) is False

    redis.incr.assert_not_awaited()
    bot.restrict_chat_member.assert_not_awaited()


async def test_one_topics_punishment_never_leaks_into_another(monkeypatch, base_data):
    """Topic 3 bans, topic 6 (inheriting) only warns — same chat, same minute."""
    rows = {
        3: _override(enabled=True, regular=60, limit=1, action="ban", duration=None),
        6: None,
    }

    async def _row(_session, _chat_id, thread_id):
        return rows.get(thread_id)

    monkeypatch.setattr(crud, "get_slow_mode_topic", _row)
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config(60, 30)))
    monkeypatch.setattr(crud, "get_scam_entry", AsyncMock(return_value=None))
    monkeypatch.setattr(crud, "add_mod_log", AsyncMock())
    redis = base_data["redis"]
    redis.get.return_value = str(int(time.time()) - 10)
    redis.incr.return_value = 1
    data = _violation_data(base_data)

    bot = make_bot()
    assert await check_and_record(bot, _group_message(topic=6), data) is False
    bot.ban_chat_member.assert_not_awaited()  # no row → warned only

    assert await check_and_record(bot, _group_message(topic=3), data) is False
    bot.ban_chat_member.assert_awaited_once()  # …its own row bans


async def test_reaching_the_limit_mutes_and_announces(monkeypatch, base_data):
    add_mod_log = AsyncMock()
    monkeypatch.setattr(crud, "add_mod_log", add_mod_log)
    data = _blocked(
        monkeypatch, base_data, limit=3, incr=3, action="mute", duration=86400
    )
    bot, msg = make_bot(), _group_message(topic=3)
    assert await check_and_record(bot, msg, data) is False

    base_data["redis"].delete.assert_awaited_once_with(
        f"slowviol:{CHAT_ID}:3:{USER_ID}"
    )  # the count starts over after a punishment
    args, _kwargs = bot.restrict_chat_member.await_args
    assert args[0] == CHAT_ID
    assert args[1] == USER_ID
    add_mod_log.assert_awaited_once()  # the moderator log gets the entry
    assert bot.send_message.await_args.args[0] == CHAT_ID
    assert bot.send_message.await_args.args[1] == "sm_punish_applied"
    assert bot.send_message.await_args.kwargs == {"parse_mode": "HTML"}


async def test_ban_action_is_used_when_configured(monkeypatch, base_data):
    monkeypatch.setattr(crud, "add_mod_log", AsyncMock())
    data = _blocked(
        monkeypatch, base_data, limit=1, incr=1, action="ban", duration=None
    )
    bot, msg = make_bot(), _group_message(topic=3)
    await check_and_record(bot, msg, data)
    bot.ban_chat_member.assert_awaited_once()
    bot.restrict_chat_member.assert_not_awaited()


async def test_kick_action_ban_then_unban(monkeypatch, base_data):
    monkeypatch.setattr(crud, "add_mod_log", AsyncMock())
    data = _blocked(
        monkeypatch, base_data, limit=1, incr=1, action="kick", duration=604800
    )
    bot, msg = make_bot(), _group_message(topic=3)
    await check_and_record(bot, msg, data)
    bot.ban_chat_member.assert_awaited_once()
    bot.unban_chat_member.assert_awaited_once()  # a kick = ban + unban


async def test_failed_punishment_is_not_announced(monkeypatch, base_data):
    """Telegram refusing the mute (no rights) must not produce a false notice."""
    monkeypatch.setattr(crud, "add_mod_log", AsyncMock())
    data = _blocked(monkeypatch, base_data, limit=1, incr=1)
    bot, msg = make_bot(), _group_message(topic=3)
    bot.restrict_chat_member.side_effect = RuntimeError("not enough rights")
    await check_and_record(bot, msg, data)
    bot.send_message.assert_not_awaited()


# --- DM screen ------------------------------------------------------------- #


async def test_punish_screen_shows_all_four_settings(base_data, fsm, monkeypatch):
    row = _topic_row(limit=5, text="Не так быстро", action="ban", duration=86400)
    _patch_screen(monkeypatch, base_data, row)
    calls = _capturing_translator(base_data)
    cb = _cb(f"dm:smw:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert next(kw for key, kw in calls if key == "dm_sm_punish_count_line") == {
        "count": 5
    }
    assert next(kw for key, kw in calls if key == "dm_sm_punish_title") == {
        "topic": "Задачи"
    }
    text = cb.message.edit_text.await_args.args[0]
    assert text.startswith("dm_sm_punish_title")  # screen body is rendered

    rows = _kb_rows(cb)
    assert [f"dm:smwt:{GROUP_CHAT_ID}:3"] in rows
    assert [f"dm:smwc:{GROUP_CHAT_ID}:3:{n}" for n in (1, 2, 3, 5, 10)] in rows
    assert [f"dm:smwc:{GROUP_CHAT_ID}:3:0"] in rows
    assert [
        f"dm:smwa:{GROUP_CHAT_ID}:3:mute",
        f"dm:smwa:{GROUP_CHAT_ID}:3:kick",
        f"dm:smwa:{GROUP_CHAT_ID}:3:ban",
    ] in rows
    assert [
        f"dm:smwd:{GROUP_CHAT_ID}:3:3600",
        f"dm:smwd:{GROUP_CHAT_ID}:3:86400",
        f"dm:smwd:{GROUP_CHAT_ID}:3:604800",
        f"dm:smwd:{GROUP_CHAT_ID}:3:0",
    ] in rows
    # «⬅️» goes back to THIS topic's grid, and the list/home stay one tap away
    assert [f"dm:smt:{GROUP_CHAT_ID}:3"] in rows
    assert [f"dm:smtl:{GROUP_CHAT_ID}", "dm:menu"] in rows

    marked = [
        btn.text
        for btn in rows_flat(cb.message.edit_text.await_args.kwargs["reply_markup"])
        if btn.text.startswith("✅ ")
    ]
    assert marked == ["✅ 5", "✅ dm_sm_punish_action_ban", "✅ dm_sm_punish_dur_days"]

    # the screen is part of the slow-mode flow: the FSM is seeded for «⬅️»
    assert await fsm.get_state() == dm_menu.DmSlowMode.awaiting_topics


async def test_punish_screen_marks_forever_and_off(base_data, fsm, monkeypatch):
    _patch_screen(
        monkeypatch, base_data, _topic_row(limit=0, duration=None, action="mute")
    )
    calls = _capturing_translator(base_data)
    cb = _cb(f"dm:smw:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert any(key == "dm_sm_punish_count_off_line" for key, _kw in calls)
    marked = [
        btn.text
        for btn in rows_flat(cb.message.edit_text.await_args.kwargs["reply_markup"])
        if btn.text.startswith("✅ ")
    ]
    assert marked == [
        "✅ dm_sm_punish_count_off",
        "✅ dm_sm_punish_action_mute",
        "✅ dm_sm_punish_dur_forever",
    ]


async def test_punish_screen_without_a_topic_limit_offers_to_set_one(
    base_data, fsm, monkeypatch
):
    """No row = nothing to punish: the screen says so and points at the grid."""
    _patch_screen(monkeypatch, base_data, None)
    calls = _capturing_translator(base_data)
    cb = _cb(f"dm:smw:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert cb.message.edit_text.await_args.args[0] == "dm_sm_punish_no_limit"
    assert next(kw for key, kw in calls if key == "dm_sm_punish_no_limit") == {
        "topic": "Задачи"
    }
    rows = _kb_rows(cb)
    assert [f"dm:smt:{GROUP_CHAT_ID}:3"] in rows  # «⚙️ Сначала задать лимит ветки»
    assert [f"dm:smtl:{GROUP_CHAT_ID}", "dm:menu"] in rows


async def test_topic_grid_carries_the_punishment_button(base_data, fsm, monkeypatch):
    """The entry lives in the topic's own settings, not on the chat-wide list."""
    monkeypatch.setattr(
        crud, "get_slow_mode_topic", AsyncMock(return_value=_topic_row())
    )
    cb = _cb(f"dm:smt:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    callbacks = _all_callbacks(cb)
    assert f"dm:smw:{GROUP_CHAT_ID}:3" in callbacks


async def test_topic_list_has_no_chat_wide_punishment_button(
    base_data, fsm, monkeypatch
):
    _patch_crud(monkeypatch, _config(), topic_override=None)
    monkeypatch.setattr(
        crud, "list_topics", AsyncMock(return_value=[_topic(3, title="Задачи")])
    )
    monkeypatch.setattr(
        crud, "list_slow_mode_topics", AsyncMock(return_value={3: _topic_row()})
    )
    cb = _cb(f"dm:smtl:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert not [
        data for data in _all_callbacks(cb) if data.startswith("dm:smpun:")
    ]  # the old chat-wide entry is gone for good
    assert not [data for data in _all_callbacks(cb) if data.startswith("dm:smw:")]
    assert f"dm:smt:{GROUP_CHAT_ID}:3" in _all_callbacks(cb)  # per-topic ⚙️ stays


async def test_punish_count_button_writes_and_redraws(base_data, fsm, monkeypatch):
    setter = _patch_screen(monkeypatch, base_data, _topic_row())
    cb = _cb(f"dm:smwc:{GROUP_CHAT_ID}:3:5")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    setter.assert_awaited_once_with(
        base_data["session"], GROUP_CHAT_ID, 3, punish_limit=5
    )
    base_data["session"].commit.assert_awaited()
    cb.answer.assert_awaited_once()
    assert cb.message.edit_text.await_args.args[0].startswith("dm_sm_punish_title")


async def test_punish_count_zero_means_never(base_data, fsm, monkeypatch):
    setter = _patch_screen(monkeypatch, base_data, _topic_row(limit=5))
    cb = _cb(f"dm:smwc:{GROUP_CHAT_ID}:3:0")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    setter.assert_awaited_once_with(
        base_data["session"], GROUP_CHAT_ID, 3, punish_limit=0
    )


async def test_punish_action_button_writes(base_data, fsm, monkeypatch):
    setter = _patch_screen(monkeypatch, base_data, _topic_row())
    cb = _cb(f"dm:smwa:{GROUP_CHAT_ID}:3:ban")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    setter.assert_awaited_once_with(
        base_data["session"], GROUP_CHAT_ID, 3, punish_action="ban"
    )


async def test_punish_action_button_refuses_junk(base_data, fsm, monkeypatch):
    setter = _patch_screen(monkeypatch, base_data, _topic_row())
    cb = _cb(f"dm:smwa:{GROUP_CHAT_ID}:3:nuke")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    setter.assert_not_awaited()  # nothing but mute/kick/ban ever reaches the DB


async def test_punish_duration_button_writes_seconds(base_data, fsm, monkeypatch):
    setter = _patch_screen(monkeypatch, base_data, _topic_row())
    cb = _cb(f"dm:smwd:{GROUP_CHAT_ID}:3:604800")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    setter.assert_awaited_once_with(
        base_data["session"], GROUP_CHAT_ID, 3, punish_duration=604800
    )


async def test_punish_duration_forever_stores_none(base_data, fsm, monkeypatch):
    setter = _patch_screen(monkeypatch, base_data, _topic_row())
    cb = _cb(f"dm:smwd:{GROUP_CHAT_ID}:3:0")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    setter.assert_awaited_once_with(
        base_data["session"], GROUP_CHAT_ID, 3, punish_duration=None
    )


async def test_punish_text_button_asks_for_the_text(base_data, fsm, monkeypatch):
    _patch_screen(monkeypatch, base_data, _topic_row())
    cb = _cb(f"dm:smwt:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert await fsm.get_state() == dm_menu.DmSlowMode.awaiting_sm_punish_text
    assert (await fsm.get_data())["thread_id"] == 3  # the step knows its topic
    assert cb.message.edit_text.await_args.args[0] == "dm_sm_punish_text_prompt"
    assert _all_callbacks(cb) == [
        f"dm:smt:{GROUP_CHAT_ID}:3",  # «⬅️ К настройкам ветки»
        f"dm:smtl:{GROUP_CHAT_ID}",
        "dm:menu",
    ]


async def test_typed_punish_text_is_saved(base_data, fsm, monkeypatch):
    setter = _patch_screen(monkeypatch, base_data, _topic_row())
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_sm_punish_text)
    await fsm.update_data(chat_id=GROUP_CHAT_ID, thread_id=3)

    msg = make_message(text="🚫 Не так быстро!", chat=_dm_chat())
    await dm_menu.dm_sm_punish_text(msg, state=fsm, **base_data)

    setter.assert_awaited_once_with(
        base_data["session"], GROUP_CHAT_ID, 3, punish_text="🚫 Не так быстро!"
    )
    assert await fsm.get_state() is None  # the step is finished
    # …and the topic's punishment screen comes back
    assert msg.answer.await_args.args[0].startswith("dm_sm_punish_title")


async def test_typed_dash_restores_the_default_text(base_data, fsm, monkeypatch):
    setter = _patch_screen(monkeypatch, base_data, _topic_row(text="старое"))
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_sm_punish_text)
    await fsm.update_data(chat_id=GROUP_CHAT_ID, thread_id=3)

    msg = make_message(text="-", chat=_dm_chat())
    await dm_menu.dm_sm_punish_text(msg, state=fsm, **base_data)
    setter.assert_awaited_once_with(
        base_data["session"], GROUP_CHAT_ID, 3, punish_text=None
    )


async def test_typed_punish_text_too_long_is_refused(base_data, fsm, monkeypatch):
    setter = _patch_screen(monkeypatch, base_data, _topic_row())
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_sm_punish_text)
    await fsm.update_data(chat_id=GROUP_CHAT_ID, thread_id=3)

    msg = make_message(text="х" * 1025, chat=_dm_chat())
    await dm_menu.dm_sm_punish_text(msg, state=fsm, **base_data)

    setter.assert_not_awaited()
    assert msg.answer.await_args.args[0] == "dm_sm_punish_text_long"
    assert await fsm.get_state() == dm_menu.DmSlowMode.awaiting_sm_punish_text


async def test_typed_punish_text_without_a_topic_returns_to_the_menu(
    base_data, fsm, monkeypatch
):
    setter = _patch_screen(monkeypatch, base_data, _topic_row())
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_sm_punish_text)
    await fsm.update_data(chat_id=GROUP_CHAT_ID)  # an old prompt, no topic

    msg = make_message(text="привет", chat=_dm_chat())
    await dm_menu.dm_sm_punish_text(msg, state=fsm, **base_data)

    setter.assert_not_awaited()
    assert msg.answer.await_args.args[0] == "dm_menu_title"


# --- labels ---------------------------------------------------------------- #


def test_punish_labels_cover_hours_days_and_forever():
    from bot.utils.text import punish_action_label, punish_duration_label

    def _(key, **kwargs):
        return key

    assert punish_duration_label(_, None) == "dm_sm_punish_dur_forever"
    assert punish_duration_label(_, 3600) == "dm_sm_punish_dur_hours"
    assert punish_duration_label(_, 604800) == "dm_sm_punish_dur_days"
    assert punish_action_label(_, "mute", 86400) == "dm_sm_punish_action_for"
    assert punish_action_label(_, "mute", None) == "dm_sm_punish_action_forever"
    assert punish_action_label(_, "kick", 3600) == "dm_sm_punish_action_kick"
