"""Tests for the slow-mode topic picker (bot/handlers/dm_menu.py).

Covers the «бот не выдаёт ветки» fix:

* an EMPTY tracked-topic list shows a hint screen — it must never silently
  save «all topics» on the user's behalf;
* a non-empty list renders topic NAMES (falling back to ``#id``);
* «🔄 Обновить список» re-reads the DB and redraws;
* «✏️ Добавить ID ветки вручную» accepts a number or a ``t.me/c/…/id`` link,
  selects that thread and returns to the picker with it visible.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.db import crud
from bot.handlers import dm_menu
from tests.conftest import make_callback, make_chat, make_message, make_user

GROUP_CHAT_ID = 12345


def _dm_chat():
    return make_chat(111, "private", "PM")


@pytest.fixture(autouse=True)
def patch_crud(monkeypatch):
    """No DB in unit tests: the slow-mode crud calls are mocked by default."""
    for name in ("list_topics", "get_slow_mode", "set_slow_mode", "get_chat"):
        monkeypatch.setattr(crud, name, AsyncMock())
    monkeypatch.setattr(crud, "set_slow_mode", AsyncMock())
    monkeypatch.setattr(crud, "list_topics", AsyncMock(return_value=[]))
    # Per-topic overrides (dict / None) — MagicMock defaults would leak into
    # the topic keyboard labels and the per-topic screens.
    monkeypatch.setattr(crud, "list_slow_mode_topics", AsyncMock(return_value={}))
    monkeypatch.setattr(crud, "get_slow_mode_topic", AsyncMock(return_value=None))
    monkeypatch.setattr(crud, "set_slow_mode_topic", AsyncMock())
    monkeypatch.setattr(crud, "clear_slow_mode_topic", AsyncMock(return_value=True))


@pytest.fixture
async def fsm():
    storage = MemoryStorage()
    ctx = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=42, chat_id=111, user_id=1000),
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


def _callbacks(kb) -> list[str]:
    return [row[0].callback_data for row in kb.inline_keyboard]


# --------------------------------------------------------------------------- #
# parse_thread_id
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("42", 42),
        ("  7 ", 7),
        ("https://t.me/c/1234/99", 99),
        ("t.me/c/1234567890/31/", 31),
        ("no digits here", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_thread_id(text, expected):
    assert dm_menu.parse_thread_id(text) == expected


# --------------------------------------------------------------------------- #
# Empty list → hint screen, NO silent «all topics» save
# --------------------------------------------------------------------------- #
async def test_empty_topics_shows_hint_and_does_not_save(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_config)
    await fsm.update_data(chat_id=GROUP_CHAT_ID)
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=None))
    monkeypatch.setattr(crud, "list_topics", AsyncMock(return_value=[]))
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode", set_mock)

    msg = make_message(text="вкл 6 3", chat=_dm_chat())
    await dm_menu.dm_slow_mode_config(msg, state=fsm, **base_data)

    # The bug: it used to save topic_ids=[] silently. It must not.
    set_mock.assert_not_awaited()
    base_data["session"].commit.assert_not_awaited()
    assert msg.answer.await_args.args[0] == "dm_sm_topics_empty"
    kb = msg.answer.await_args.kwargs["reply_markup"]
    assert _callbacks(kb) == [
        f"dm:smball:{GROUP_CHAT_ID}",
        f"dm:smadd:{GROUP_CHAT_ID}",
        f"dm:smback:{GROUP_CHAT_ID}",
    ]
    # Pending config is retained so «Все ветки» can still save it.
    state_data = await fsm.get_data()
    assert await fsm.get_state() == dm_menu.DmSlowMode.awaiting_topics
    assert state_data["pending_sm"] == {
        "enabled": True,
        "regular": 21600,
        "wl": 10800,
    }
    assert state_data["selected_topics"] == []


async def test_hint_all_topics_button_saves_whole_chat(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_topics)
    await fsm.update_data(
        chat_id=GROUP_CHAT_ID,
        selected_topics=[],
        pending_sm={"enabled": True, "regular": 21600, "wl": 10800},
    )
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode", set_mock)

    cb = _cb(f"dm:smball:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    set_mock.assert_awaited_once_with(
        base_data["session"],
        GROUP_CHAT_ID,
        enabled=True,
        regular_seconds=21600,
        wl_seconds=10800,
        topic_ids=[],
    )
    assert await fsm.get_state() is None


# --------------------------------------------------------------------------- #
# Non-empty list → names in the buttons
# --------------------------------------------------------------------------- #
async def test_topics_show_names_with_id_fallback(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_config)
    await fsm.update_data(chat_id=GROUP_CHAT_ID)
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=None))
    monkeypatch.setattr(
        crud,
        "list_topics",
        AsyncMock(
            return_value=[
                _topic(3, count=10, title="Новости"),
                _topic(6, count=4, title=None),
            ]
        ),
    )

    msg = make_message(text="вкл 6 3", chat=_dm_chat())
    await dm_menu.dm_slow_mode_config(msg, state=fsm, **base_data)

    assert msg.answer.await_args.args[0] == "dm_sm_topics_prompt"
    kb = msg.answer.await_args.kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].text == "☑️ Новости · 10 сообщ."
    assert kb.inline_keyboard[0][0].callback_data == f"dm:smb:{GROUP_CHAT_ID}:3"
    assert kb.inline_keyboard[1][0].text == "☑️ #6 · 4 сообщ."
    assert kb.inline_keyboard[1][0].callback_data == f"dm:smb:{GROUP_CHAT_ID}:6"
    # «Все ветки», «Готово», «Обновить», «Добавить ID», then the nav row.
    assert _callbacks(kb)[2:6] == [
        f"dm:smball:{GROUP_CHAT_ID}",
        f"dm:smbdone:{GROUP_CHAT_ID}",
        f"dm:smrefresh:{GROUP_CHAT_ID}",
        f"dm:smadd:{GROUP_CHAT_ID}",
    ]


# --------------------------------------------------------------------------- #
# «🔄 Обновить список» re-reads the DB
# --------------------------------------------------------------------------- #
async def test_refresh_rereads_topics(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_topics)
    await fsm.update_data(
        chat_id=GROUP_CHAT_ID,
        selected_topics=[3],
        pending_sm={"enabled": True, "regular": 21600, "wl": 10800},
    )
    list_mock = AsyncMock(return_value=[_topic(3, count=2, title="Свежая ветка")])
    monkeypatch.setattr(crud, "list_topics", list_mock)

    cb = _cb(f"dm:smrefresh:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    list_mock.assert_awaited_once_with(base_data["session"], GROUP_CHAT_ID)
    assert cb.message.edit_text.await_args.args[0] == "dm_sm_topics_prompt"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].text.startswith("✅")
    assert "Свежая ветка" in kb.inline_keyboard[0][0].text
    cb.answer.assert_awaited_once()


async def test_refresh_still_empty_keeps_hint_screen(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_topics)
    await fsm.update_data(chat_id=GROUP_CHAT_ID, selected_topics=[], pending_sm={})
    monkeypatch.setattr(crud, "list_topics", AsyncMock(return_value=[]))

    cb = _cb(f"dm:smrefresh:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert cb.message.edit_text.await_args.args[0] == "dm_sm_topics_empty"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert _callbacks(kb) == [
        f"dm:smball:{GROUP_CHAT_ID}",
        f"dm:smadd:{GROUP_CHAT_ID}",
        f"dm:smback:{GROUP_CHAT_ID}",
    ]


# --------------------------------------------------------------------------- #
# «✏️ Добавить ID ветки вручную» → FSM → selected and visible
# --------------------------------------------------------------------------- #
async def test_add_id_button_starts_fsm(base_data, fsm):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_topics)
    await fsm.update_data(chat_id=GROUP_CHAT_ID, selected_topics=[], pending_sm={})

    cb = _cb(f"dm:smadd:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert await fsm.get_state() == dm_menu.DmSlowMode.awaiting_topic_id
    assert cb.message.edit_text.await_args.args[0] == "dm_topic_id_prompt"


async def test_manual_id_is_selected_and_visible(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_topic_id)
    await fsm.update_data(chat_id=GROUP_CHAT_ID, selected_topics=[], pending_sm={})
    monkeypatch.setattr(crud, "list_topics", AsyncMock(return_value=[]))

    msg = make_message(text="https://t.me/c/1234/777", chat=_dm_chat())
    await dm_menu.dm_sm_topic_id(msg, state=fsm, **base_data)

    assert await fsm.get_state() == dm_menu.DmSlowMode.awaiting_topics
    assert (await fsm.get_data())["selected_topics"] == [777]
    kb = msg.answer.await_args.kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].text.startswith("✅")
    assert kb.inline_keyboard[0][0].callback_data == f"dm:smb:{GROUP_CHAT_ID}:777"


async def test_manual_id_bad_input_keeps_state(base_data, fsm):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_topic_id)
    await fsm.update_data(chat_id=GROUP_CHAT_ID, selected_topics=[])

    msg = make_message(text="нет цифр", chat=_dm_chat())
    await dm_menu.dm_sm_topic_id(msg, state=fsm, **base_data)

    assert msg.answer.await_args.args[0] == "dm_topic_id_bad"
    assert await fsm.get_state() == dm_menu.DmSlowMode.awaiting_topic_id


async def test_manual_id_done_saves_selection(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_topic_id)
    await fsm.update_data(chat_id=GROUP_CHAT_ID, selected_topics=[])
    monkeypatch.setattr(crud, "list_topics", AsyncMock(return_value=[]))
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode", set_mock)

    msg = make_message(text="5", chat=_dm_chat())
    await dm_menu.dm_sm_topic_id(msg, state=fsm, **base_data)

    cb = _cb(f"dm:smbdone:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    set_mock.assert_awaited_once_with(
        base_data["session"],
        GROUP_CHAT_ID,
        enabled=True,
        regular_seconds=21600,
        wl_seconds=10800,
        topic_ids=[5],
    )


async def test_smback_clears_state_and_shows_panel(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_topics)
    await fsm.update_data(chat_id=GROUP_CHAT_ID, selected_topics=[], pending_sm={})
    monkeypatch.setattr(
        crud,
        "get_chat",
        AsyncMock(return_value=SimpleNamespace(chat_id=GROUP_CHAT_ID, title="G")),
    )

    cb = _cb(f"dm:smback:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert await fsm.get_state() is None
    assert cb.message.edit_text.await_args.args[0] == "dm_panel_title"


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_scope_summary_single_hash(lang):
    """«Ветки: #3, #6» — ids already carry '#', the template must not add one."""
    from bot.i18n.loader import get_i18n

    def _(key: str, **kw):
        return get_i18n().get(key, lang, **kw)

    assert dm_menu._sm_topics_summary(_, [3, 6]) == "#3, #6"
    assert dm_menu._sm_topics_summary(_, []) == get_i18n().get(
        "dm_sm_topics_summary_all", lang
    )
