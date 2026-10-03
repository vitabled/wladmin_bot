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


def _override(enabled=True, regular=None, wl=None) -> SimpleNamespace:
    return SimpleNamespace(enabled=enabled, regular_seconds=regular, wl_seconds=wl)


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


async def test_chat_disabled_wins_over_override(monkeypatch, base_data):
    _patch_crud(
        monkeypatch,
        _config(enabled=False, regular=60, wl=30),
        topic_override=_override(enabled=True, regular=30, wl=15),
    )
    redis = base_data["redis"]
    msg = _group_message(topic=42)
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
        [f"dm:smb:{GROUP_CHAT_ID}:3", f"dm:smt:{GROUP_CHAT_ID}:3"],
        [f"dm:smb:{GROUP_CHAT_ID}:6", f"dm:smt:{GROUP_CHAT_ID}:6"],
        [f"dm:smball:{GROUP_CHAT_ID}"],
        [f"dm:smbdone:{GROUP_CHAT_ID}"],
        [f"dm:smrefresh:{GROUP_CHAT_ID}"],
        [f"dm:smadd:{GROUP_CHAT_ID}"],
        [f"dm:g:{GROUP_CHAT_ID}", "dm:menu"],
    ]
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].text == "✅ Новости · 10 сообщ."
    assert kb.inline_keyboard[0][1].text == "dm_sm_topic_params"
    assert kb.inline_keyboard[0][1].icon_custom_emoji_id == "5877260593903177342"
    # Overrides show up on the row (the test translator echoes the key):
    # «· dm_sm_topic_status_off» = the rule is off in that topic.
    assert kb.inline_keyboard[1][0].text == "☑️ #6 · 4 сообщ. · dm_sm_topic_status_off"
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
        kb.inline_keyboard[0][0].text
        == "☑️ Новости · 2 сообщ. · dm_sm_topic_status_own"
    )


async def test_topics_list_empty_shows_hint_screen(base_data, fsm, monkeypatch):
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=None))
    monkeypatch.setattr(crud, "list_topics", AsyncMock(return_value=[]))

    cb = _cb(f"dm:smtl:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert cb.message.edit_text.await_args.args[0] == "dm_sm_topics_empty"
    assert _all_callbacks(cb) == [
        f"dm:smball:{GROUP_CHAT_ID}",
        f"dm:smadd:{GROUP_CHAT_ID}",
        f"dm:smback:{GROUP_CHAT_ID}",
    ]


async def test_topic_screen_inherits_chat(base_data, fsm, monkeypatch):
    calls = _capturing_translator(base_data)
    monkeypatch.setattr(
        crud, "get_slow_mode", AsyncMock(return_value=_config(regular=21600, wl=10800))
    )
    monkeypatch.setattr(
        crud, "list_topics", AsyncMock(return_value=[_topic(3, title="Новости")])
    )

    cb = _cb(f"dm:smt:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert cb.message.edit_text.await_args.args[0] == "dm_sm_topic_screen"
    screen = next(kw for key, kw in calls if key == "dm_sm_topic_screen")
    assert screen["topic"] == "Новости"
    assert screen["state"] == "dm_sm_topic_state_inherit"
    assert (screen["regular"], screen["wl"]) == (6, 3)
    assert next(kw for key, kw in calls if key == "dm_sm_topic_state_inherit") == {
        "regular": 6,
        "wl": 3,
    }
    assert _kb_rows(cb) == [
        [f"dm:smtx:{GROUP_CHAT_ID}:3"],
        [f"dm:smtv:{GROUP_CHAT_ID}:3"],
        [f"dm:smtl:{GROUP_CHAT_ID}", "dm:menu"],
    ]
    # No override yet → no «Как в чате» row.
    assert f"dm:smtr:{GROUP_CHAT_ID}:3" not in _all_callbacks(cb)


async def test_topic_screen_own_interval(base_data, fsm, monkeypatch):
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
    assert f"dm:smtr:{GROUP_CHAT_ID}:3" in _all_callbacks(cb)


async def test_topic_screen_off_here_and_chat_off(base_data, fsm, monkeypatch):
    calls = _capturing_translator(base_data)
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    monkeypatch.setattr(
        crud, "get_slow_mode_topic", AsyncMock(return_value=_override(enabled=False))
    )

    cb = _cb(f"dm:smt:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert any(key == "dm_sm_topic_state_off" for key, _kw in calls)
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].text == "dm_sm_topic_switch_on"

    calls.clear()
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=None))
    cb = _cb(f"dm:smt:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert any(key == "dm_sm_topic_state_chat_off" for key, _kw in calls)


async def test_topic_switch_flips_and_commits(base_data, fsm, monkeypatch):
    row = _override(enabled=True)
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    monkeypatch.setattr(crud, "get_slow_mode_topic", AsyncMock(return_value=row))
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)

    cb = _cb(f"dm:smtx:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    set_mock.assert_awaited_once_with(
        base_data["session"], GROUP_CHAT_ID, 3, enabled=False
    )
    base_data["session"].commit.assert_awaited_once()
    assert cb.message.edit_text.await_args.args[0] == "dm_sm_topic_screen"
    cb.answer.assert_awaited_once()


async def test_topic_switch_without_row_enables_then_disables(
    base_data, fsm, monkeypatch
):
    """A missing row counts as «following the chat», so the first press = off."""
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    monkeypatch.setattr(crud, "get_slow_mode_topic", AsyncMock(return_value=None))
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)

    cb = _cb(f"dm:smtx:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    set_mock.assert_awaited_once_with(
        base_data["session"], GROUP_CHAT_ID, 3, enabled=False
    )


async def test_topic_reset_clears_override(base_data, fsm, monkeypatch):
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    clear_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(crud, "clear_slow_mode_topic", clear_mock)

    cb = _cb(f"dm:smtr:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    clear_mock.assert_awaited_once_with(base_data["session"], GROUP_CHAT_ID, 3)
    base_data["session"].commit.assert_awaited_once()
    assert cb.message.edit_text.await_args.args[0] == "dm_sm_topic_screen"


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
        [f"dm:smt:{GROUP_CHAT_ID}:3", "dm:menu"],
    ]


# --- per-topic intervals by buttons (no typing) ----------------------------- #


def _btn_texts(cb) -> list[str]:
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    return [btn.text for row in kb.inline_keyboard for btn in row]


async def test_topic_screen_interval_buttons_carry_effective_hours(
    base_data, fsm, monkeypatch
):
    """Buttons on the topic screen name the effective hours (own beats chat)."""
    calls: list[tuple[str, dict]] = []

    def raw(key, **kw):
        calls.append((key, kw))
        return key

    base_data["_raw"] = raw
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

    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert kb.inline_keyboard[1][0].callback_data == f"dm:smtv:{GROUP_CHAT_ID}:3"
    assert ("dm_sm_hours", {"hours": 2}) in calls  # own value wins


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
        [f"dm:smt:{GROUP_CHAT_ID}:3", "dm:menu"],
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
        base_data["session"], GROUP_CHAT_ID, 3, regular_seconds=3600, wl_seconds=None
    )


async def test_topic_pick_set_pins_value_and_redraws(base_data, fsm, monkeypatch):
    """A preset press pins the topic's limit for everyone and redraws the grid."""
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)

    cb = _cb(f"dm:smtvs:{GROUP_CHAT_ID}:3:12")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    set_mock.assert_awaited_once_with(
        base_data["session"], GROUP_CHAT_ID, 3, regular_seconds=43200, wl_seconds=None
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
        base_data["session"], GROUP_CHAT_ID, 3, regular_seconds=0, wl_seconds=None
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
        base_data["session"], GROUP_CHAT_ID, 3, regular_seconds=expected, wl_seconds=None
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
        base_data["session"], GROUP_CHAT_ID, 3, regular_seconds=3600, wl_seconds=None
    )


async def test_topic_pick_inherit_clears_own_interval(base_data, fsm, monkeypatch):
    """«↩️ Как в чате» in the grid stores NULL so the chat's rules apply again."""
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    monkeypatch.setattr(
        crud,
        "get_slow_mode_topic",
        AsyncMock(return_value=_override(enabled=True, regular=7200, wl=3600)),
    )
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)

    cb = _cb(f"dm:smtvi:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    set_mock.assert_awaited_once_with(
        base_data["session"], GROUP_CHAT_ID, 3, regular_seconds=None, wl_seconds=None
    )
    assert cb.message.edit_text.await_args.args[0].startswith("dm_sm_topic_pick_all")


async def test_topic_pick_hides_inherit_without_own_value(base_data, fsm, monkeypatch):
    """While the topic has no value of its own there is nothing to reset."""
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))

    cb = _cb(f"dm:smtv:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert f"dm:smtvi:{GROUP_CHAT_ID}:3" not in _all_callbacks(cb)
    # …но ручной ввод и возврат к ветке остаются доступными.
    assert f"dm:smtp:{GROUP_CHAT_ID}:3" in _all_callbacks(cb)
    assert f"dm:smt:{GROUP_CHAT_ID}:3" in _all_callbacks(cb)


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
        {"regular_seconds": 21600, "wl_seconds": None},
        {"enabled": True},
    ]
    base_data["session"].commit.assert_awaited_once_with()
    assert await fsm.get_state() is None
    assert msg.answer.await_args.args[0] == "dm_sm_topic_screen"


@pytest.mark.parametrize("text", ["вкл 6", "ВКЛ 6", "6", "6 ч", "6h"])
async def test_params_accepts_prefix_case_and_unit(base_data, fsm, monkeypatch, text):
    await _await_params(fsm)
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=_config()))
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode_topic", set_mock)

    msg = make_message(text=text, chat=_dm_chat())
    await dm_menu.dm_sm_topic_params(msg, state=fsm, **base_data)

    _args, kwargs = set_mock.await_args_list[0]
    assert kwargs == {"regular_seconds": 21600, "wl_seconds": None}


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
    assert kwargs == {"regular_seconds": 0, "wl_seconds": None}


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
    assert msg.answer.await_args.args[0] == "dm_sm_topic_screen"


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
