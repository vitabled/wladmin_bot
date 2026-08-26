"""Handler tests for the DM TABBED menu (bot/handlers/dm_menu.py).

Covers the webapp-mirroring tabs:

* Администрирование (``dm:tab:admin``) → groups list → per-group panel
  (moderation + settings + slow mode + stats/top, no scam/WL);
* Рейтинги (``dm:tab:ratings``) → seller check (``DmScam``), whitelist
  add/remove (``DmWl``) and the scam/WL list;
* stats/top live on the group panel (``dm:a:stats:<chat_id>`` /
  ``dm:a:top:<chat_id>``) — there is no separate Статистика tab;
* Рассылки (``dm:tab:broadcast``) → group picker → topic multi-select →
  ``DmBroadcast`` text flow via ``send_broadcast``;
* slow mode (``dm:sm:<chat_id>``) and the universal ``dm:menu`` bail-out.

Follows the established direct-handler style: mocked messages/callbacks from
tests/conftest.py and a real FSMContext backed by MemoryStorage.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.constants import SCAM_SOURCE_SCAM, SCAM_SOURCE_VERIFIED
from bot.db import crud
from bot.handlers import dm_menu
from bot.utils.targets import Target
from tests.conftest import make_callback, make_chat, make_message, make_user

GROUP_CHAT_ID = 12345


def _translate(key, **kwargs):
    return key


def _dm_chat():
    return make_chat(111, "private", "PM")


@pytest.fixture(autouse=True)
def patch_crud(monkeypatch):
    """No DB in unit tests: every tab crud call is mocked by default."""
    for name in (
        "list_active_chats",
        "get_chat",
        "list_topics",
        "get_slow_mode",
        "set_slow_mode",
        "list_scam_entries",
        "get_users_by_ids",
        "chat_activity_totals",
        "count_bans",
        "count_warns_chat",
        "top_active",
        "upsert_scam_entry",
        "remove_scam_entry",
        "get_scam_entry",
        "get_user_by_username",
    ):
        monkeypatch.setattr(crud, name, AsyncMock())
    monkeypatch.setattr(crud, "get_scam_entry", AsyncMock(return_value=None))
    monkeypatch.setattr(crud, "get_user_by_username", AsyncMock(return_value=None))


@pytest.fixture
async def fsm():
    """A real FSMContext on MemoryStorage (chat 111 / user 1000)."""
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
    cb.message.delete = AsyncMock()
    cb.message.edit_reply_markup = AsyncMock()
    return cb


def _group(name: str = "Test Group"):
    return SimpleNamespace(chat_id=GROUP_CHAT_ID, title=name)


def _chats(n: int, prefix: str = "Group"):
    return [SimpleNamespace(chat_id=1000 + i, title=f"{prefix} {i}") for i in range(n)]


def _topic(thread_id: int, count: int = 1):
    return SimpleNamespace(thread_id=thread_id, message_count=count, last_seen=None)


def _capturing_translator(base_data):
    """Replace ``_`` with one that records (key, kwargs) for assertions."""
    calls: list[tuple[str, dict]] = []

    def t(key, **kwargs):
        calls.append((key, kwargs))
        return key

    base_data["_"] = t
    return calls


# --------------------------------------------------------------------------- #
# Main menu tabs
# --------------------------------------------------------------------------- #
def test_main_menu_has_three_tabs_no_scam_or_groups():
    kb = dm_menu.build_main_menu(_translate)
    rows = kb.inline_keyboard
    # WebApp panel row + 3 tab rows + info/help row.
    assert len(rows) == 5
    assert [len(row) for row in rows] == [1, 1, 1, 1, 2]
    # Row 0 is the WebApp button (web_app set, no callback_data).
    assert rows[0][0].web_app is not None
    assert rows[0][0].callback_data is None
    callbacks = [row[0].callback_data for row in rows[1:4]]
    callbacks += [btn.callback_data for btn in rows[4]]
    assert callbacks == [
        "dm:tab:admin",
        "dm:tab:ratings",
        "dm:tab:broadcast",
        "dm:info",
        "dm:help",
    ]
    assert "dm:tab:stats" not in callbacks
    assert "dm:scam" not in callbacks
    assert "dm:groups" not in callbacks


# --------------------------------------------------------------------------- #
# Администрирование tab
# --------------------------------------------------------------------------- #
async def test_tab_admin_shows_groups(base_data, fsm, monkeypatch):
    monkeypatch.setattr(crud, "list_active_chats", AsyncMock(return_value=_chats(2)))
    cb = _cb("dm:tab:admin")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert cb.message.edit_text.await_args.args[0] == "dm_groups_title"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    callbacks = [row[0].callback_data for row in kb.inline_keyboard]
    assert callbacks == ["dm:g:1000", "dm:g:1001", "dm:menu"]
    cb.answer.assert_awaited_once()


async def test_tab_admin_groups_pagination(base_data, fsm, monkeypatch):
    monkeypatch.setattr(crud, "list_active_chats", AsyncMock(return_value=_chats(10)))
    cb = _cb("dm:tab:admin")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    callbacks = [row[0].callback_data for row in kb.inline_keyboard]
    assert callbacks[:8] == [f"dm:g:{1000 + i}" for i in range(8)]
    assert callbacks[8] == "dm:gp:1"
    assert callbacks[9] == "dm:menu"


async def test_tab_admin_group_panel_has_slowmode_stats_top_no_scam_wl(
    base_data, fsm, monkeypatch
):
    monkeypatch.setattr(crud, "get_chat", AsyncMock(return_value=_group()))
    cb = _cb(f"dm:g:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    for act in ("ban", "kick", "mute", "warn", "unban", "unmute", "warns", "unwarn"):
        assert f"dm:a:{act}:{GROUP_CHAT_ID}" in callbacks
    assert f"dm:a:settings:{GROUP_CHAT_ID}" in callbacks
    assert f"dm:sm:{GROUP_CHAT_ID}" in callbacks  # slow mode on the panel
    assert f"dm:a:stats:{GROUP_CHAT_ID}" in callbacks  # stats on the panel
    assert f"dm:a:top:{GROUP_CHAT_ID}" in callbacks  # top on the panel
    assert "dm:groups" in callbacks
    assert "dm:menu" in callbacks
    for banned in ("scam", "wl", "wl_remove"):
        assert f"dm:a:{banned}:{GROUP_CHAT_ID}" not in callbacks


# --------------------------------------------------------------------------- #
# Slow mode (dm:sm:<chat_id> → DmSlowMode.awaiting_config)
# --------------------------------------------------------------------------- #
async def test_slow_mode_callback_sets_state(base_data, fsm, monkeypatch):
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=None))
    cb = _cb(f"dm:sm:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert await fsm.get_state() == dm_menu.DmSlowMode.awaiting_config
    assert (await fsm.get_data())["chat_id"] == GROUP_CHAT_ID
    assert cb.message.edit_text.await_args.args[0] == "dm_sm_prompt"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == [
        f"dm:g:{GROUP_CHAT_ID}",
        "dm:menu",
    ]
    cb.answer.assert_awaited_once()


async def test_slow_mode_on_sets_config_and_clears_state(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_config)
    await fsm.update_data(chat_id=GROUP_CHAT_ID)
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=None))
    monkeypatch.setattr(crud, "list_topics", AsyncMock(return_value=[]))
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode", set_mock)
    calls = _capturing_translator(base_data)

    msg = make_message(text="вкл 6 3", chat=_dm_chat())
    await dm_menu.dm_slow_mode_config(msg, state=fsm, **base_data)

    set_mock.assert_awaited_once_with(
        base_data["session"],
        GROUP_CHAT_ID,
        enabled=True,
        regular_seconds=21600,
        wl_seconds=10800,
        topic_ids=[],  # no tracked topics → whole chat
    )
    base_data["session"].commit.assert_awaited_once()
    assert await fsm.get_state() is None  # state cleared
    saved_kwargs = next(v for k, v in calls if k == "dm_sm_saved")
    assert saved_kwargs == {
        "regular": 6,
        "wl": 3,
        "topics": "dm_sm_topics_summary_all",
    }
    kb = msg.answer.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == [
        f"dm:g:{GROUP_CHAT_ID}",
        "dm:menu",
    ]


async def test_slow_mode_off_disables(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_config)
    await fsm.update_data(chat_id=GROUP_CHAT_ID)
    monkeypatch.setattr(
        crud,
        "get_slow_mode",
        AsyncMock(
            return_value=SimpleNamespace(
                enabled=True, regular_seconds=21600, wl_seconds=10800, topic_ids=[3]
            )
        ),
    )
    list_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(crud, "list_topics", list_mock)
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode", set_mock)
    calls = _capturing_translator(base_data)

    msg = make_message(text="выкл", chat=_dm_chat())
    await dm_menu.dm_slow_mode_config(msg, state=fsm, **base_data)

    set_mock.assert_awaited_once_with(base_data["session"], GROUP_CHAT_ID, enabled=False)
    base_data["session"].commit.assert_awaited_once()
    assert await fsm.get_state() is None
    list_mock.assert_not_awaited()  # off skips the topics step entirely
    saved_kwargs = next(v for k, v in calls if k == "dm_sm_saved")
    assert saved_kwargs == {
        "regular": 6,
        "wl": 3,  # old values reported
        "topics": "dm_sm_topics_summary_list",  # stored scope still shown
    }


async def test_slow_mode_bad_format_keeps_state(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_config)
    await fsm.update_data(chat_id=GROUP_CHAT_ID)
    msg = make_message(text="вкл abc", chat=_dm_chat())
    await dm_menu.dm_slow_mode_config(msg, state=fsm, **base_data)
    assert msg.answer.await_args.args[0] == "dm_sm_bad"
    assert await fsm.get_state() == dm_menu.DmSlowMode.awaiting_config
    crud.set_slow_mode.assert_not_awaited()


async def test_slow_mode_garbage_word_keeps_state(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_config)
    await fsm.update_data(chat_id=GROUP_CHAT_ID)
    msg = make_message(text="banana 1 2", chat=_dm_chat())
    await dm_menu.dm_slow_mode_config(msg, state=fsm, **base_data)
    assert msg.answer.await_args.args[0] == "dm_sm_bad"
    assert await fsm.get_state() == dm_menu.DmSlowMode.awaiting_config


async def test_slow_mode_clamps_hours(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_config)
    await fsm.update_data(chat_id=GROUP_CHAT_ID)
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=None))
    monkeypatch.setattr(crud, "list_topics", AsyncMock(return_value=[]))
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode", set_mock)
    msg = make_message(text="вкл 9999 0", chat=_dm_chat())
    await dm_menu.dm_slow_mode_config(msg, state=fsm, **base_data)
    # clamped to [1, 720]
    set_mock.assert_awaited_once_with(
        base_data["session"],
        GROUP_CHAT_ID,
        enabled=True,
        regular_seconds=720 * 3600,
        wl_seconds=1 * 3600,
        topic_ids=[],
    )


# --- slow-mode topic multi-select (DmSlowMode.awaiting_topics) ------------- #

def _topics(*thread_ids):
    return [SimpleNamespace(thread_id=t, message_count=10 * t, last_seen=None) for t in thread_ids]


async def test_slow_mode_on_with_topics_goes_to_awaiting_topics(
    base_data, fsm, monkeypatch
):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_config)
    await fsm.update_data(chat_id=GROUP_CHAT_ID)
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=None))
    monkeypatch.setattr(crud, "list_topics", AsyncMock(return_value=_topics(3, 6, 45)))
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode", set_mock)

    msg = make_message(text="вкл 6 3", chat=_dm_chat())
    await dm_menu.dm_slow_mode_config(msg, state=fsm, **base_data)

    set_mock.assert_not_awaited()  # pending — nothing saved yet
    assert await fsm.get_state() == dm_menu.DmSlowMode.awaiting_topics
    state_data = await fsm.get_data()
    assert state_data["pending_sm"] == {"enabled": True, "regular": 21600, "wl": 10800}
    assert state_data["selected_topics"] == []
    assert msg.answer.await_args.args[0] == "dm_sm_topics_prompt"
    kb = msg.answer.await_args.kwargs["reply_markup"]
    rows = kb.inline_keyboard
    assert rows[0][0].callback_data == f"dm:smb:{GROUP_CHAT_ID}:3"
    assert rows[0][0].text == f"☑️ #3 · {3 * 10} сообщ."
    assert rows[3][0].callback_data == f"dm:smball:{GROUP_CHAT_ID}"
    assert rows[4][0].callback_data == f"dm:smbdone:{GROUP_CHAT_ID}"
    # nav row: panel back + home
    assert [btn.callback_data for btn in rows[5]] == [
        f"dm:g:{GROUP_CHAT_ID}",
        "dm:menu",
    ]


async def test_slow_mode_on_prefills_selection_from_config(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_config)
    await fsm.update_data(chat_id=GROUP_CHAT_ID)
    monkeypatch.setattr(
        crud,
        "get_slow_mode",
        AsyncMock(
            return_value=SimpleNamespace(
                enabled=False, regular_seconds=21600, wl_seconds=10800, topic_ids=[3]
            )
        ),
    )
    monkeypatch.setattr(crud, "list_topics", AsyncMock(return_value=_topics(3, 6)))

    msg = make_message(text="вкл 6 3", chat=_dm_chat())
    await dm_menu.dm_slow_mode_config(msg, state=fsm, **base_data)

    state_data = await fsm.get_data()
    assert state_data["selected_topics"] == [3]  # prefilled from the row
    kb = msg.answer.await_args.kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].text.startswith("✅")  # #3 pre-checked
    assert kb.inline_keyboard[1][0].text.startswith("☑️")  # #6 unchecked


async def test_slow_mode_topic_toggle_updates_selection(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_topics)
    await fsm.update_data(
        chat_id=GROUP_CHAT_ID,
        selected_topics=[],
        pending_sm={"enabled": True, "regular": 21600, "wl": 10800},
    )
    monkeypatch.setattr(crud, "list_topics", AsyncMock(return_value=_topics(3, 6)))
    cb = _cb(f"dm:smb:{GROUP_CHAT_ID}:3")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert (await fsm.get_data())["selected_topics"] == [3]
    assert await fsm.get_state() == dm_menu.DmSlowMode.awaiting_topics  # still picking
    kb = cb.message.edit_reply_markup.await_args.kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].text.startswith("✅")
    cb.answer.assert_awaited_once()


async def test_slow_mode_topics_done_saves_selection(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_topics)
    await fsm.update_data(
        chat_id=GROUP_CHAT_ID,
        selected_topics=[3, 6],
        pending_sm={"enabled": True, "regular": 21600, "wl": 10800},
    )
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode", set_mock)
    calls = _capturing_translator(base_data)

    cb = _cb(f"dm:smbdone:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    set_mock.assert_awaited_once_with(
        base_data["session"],
        GROUP_CHAT_ID,
        enabled=True,
        regular_seconds=21600,
        wl_seconds=10800,
        topic_ids=[3, 6],
    )
    base_data["session"].commit.assert_awaited_once()
    assert await fsm.get_state() is None
    saved_kwargs = next(v for k, v in calls if k == "dm_sm_saved")
    assert saved_kwargs == {"regular": 6, "wl": 3, "topics": "dm_sm_topics_summary_list"}
    ids_kwargs = next(v for k, v in calls if k == "dm_sm_topics_summary_list")
    assert ids_kwargs == {"ids": "#3, #6"}


async def test_slow_mode_topics_all_saves_empty_scope(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_topics)
    await fsm.update_data(
        chat_id=GROUP_CHAT_ID,
        selected_topics=[3],
        pending_sm={"enabled": True, "regular": 7200, "wl": 3600},
    )
    set_mock = AsyncMock()
    monkeypatch.setattr(crud, "set_slow_mode", set_mock)
    calls = _capturing_translator(base_data)

    cb = _cb(f"dm:smball:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    set_mock.assert_awaited_once_with(
        base_data["session"],
        GROUP_CHAT_ID,
        enabled=True,
        regular_seconds=7200,
        wl_seconds=3600,
        topic_ids=[],  # «Все ветки» → whole chat
    )
    assert await fsm.get_state() is None
    saved_kwargs = next(v for k, v in calls if k == "dm_sm_saved")
    assert saved_kwargs["topics"] == "dm_sm_topics_summary_all"


# --------------------------------------------------------------------------- #
# Рейтинги tab
# --------------------------------------------------------------------------- #
async def test_tab_ratings_buttons(base_data, fsm):
    cb = _cb("dm:tab:ratings")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    callbacks = [row[0].callback_data for row in kb.inline_keyboard]
    assert callbacks == [
        "dm:rt:check",
        "dm:rt:wl",
        "dm:rt:wlrm",
        "dm:rt:list",
        "dm:menu",
    ]
    cb.answer.assert_awaited_once()


async def test_rt_check_starts_dmscam_with_chat_id_none(base_data, fsm):
    cb = _cb("dm:rt:check")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert await fsm.get_state() == dm_menu.DmScam.awaiting_target
    state_data = await fsm.get_data()
    assert state_data.get("chat_id") is None
    assert cb.message.edit_text.await_args.args[0] == "dm_rt_check_prompt"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == ["dm:menu"]


async def test_rt_wl_starts_dmwl_add(base_data, fsm):
    cb = _cb("dm:rt:wl")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert await fsm.get_state() == dm_menu.DmWl.awaiting_target
    assert (await fsm.get_data())["action"] == "add"
    assert cb.message.edit_text.await_args.args[0] == "dm_rt_wl_prompt"


async def test_rt_wlrm_starts_dmwl_remove(base_data, fsm):
    cb = _cb("dm:rt:wlrm")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert await fsm.get_state() == dm_menu.DmWl.awaiting_target
    assert (await fsm.get_data())["action"] == "remove"
    assert cb.message.edit_text.await_args.args[0] == "dm_rt_wlrm_prompt"


async def test_dm_wl_target_adds_verified(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmWl.awaiting_target)
    await fsm.update_data(action="add")
    monkeypatch.setattr(
        dm_menu,
        "resolve_target",
        AsyncMock(return_value=(Target(500, "Victim", "victim"), None, 1)),
    )
    msg = make_message(text="@victim", chat=_dm_chat())
    await dm_menu.dm_wl_target(msg, state=fsm, **base_data)
    crud.upsert_scam_entry.assert_awaited_once_with(
        base_data["session"], 500, SCAM_SOURCE_VERIFIED, None
    )
    assert "addtowl_added" in msg.answer.await_args.args[0]
    assert msg.answer.await_args.kwargs["parse_mode"] == "HTML"
    kb = msg.answer.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == ["dm:menu"]
    assert await fsm.get_state() is None


async def test_dm_wl_target_removes(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmWl.awaiting_target)
    await fsm.update_data(action="remove")
    monkeypatch.setattr(
        dm_menu,
        "resolve_target",
        AsyncMock(return_value=(Target(500, "Victim", "victim"), None, 1)),
    )
    monkeypatch.setattr(crud, "remove_scam_entry", AsyncMock(return_value=True))
    msg = make_message(text="@victim", chat=_dm_chat())
    await dm_menu.dm_wl_target(msg, state=fsm, **base_data)
    crud.remove_scam_entry.assert_awaited_once_with(base_data["session"], 500)
    assert "addtowl_removed" in msg.answer.await_args.args[0]
    assert await fsm.get_state() is None


async def test_dm_wl_target_error_keeps_state(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmWl.awaiting_target)
    await fsm.update_data(action="add")
    monkeypatch.setattr(
        dm_menu,
        "resolve_target",
        AsyncMock(return_value=(None, "error_no_target", 0)),
    )
    msg = make_message(text="not-a-target", chat=_dm_chat())
    await dm_menu.dm_wl_target(msg, state=fsm, **base_data)
    assert "scam_no_target" in msg.answer.await_args.args[0]
    kb = msg.answer.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == ["dm:menu"]
    assert await fsm.get_state() == dm_menu.DmWl.awaiting_target  # kept


async def test_dm_wl_target_bot_is_no_target(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmWl.awaiting_target)
    await fsm.update_data(action="add")
    monkeypatch.setattr(
        dm_menu,
        "resolve_target",
        AsyncMock(return_value=(Target(42, "Bot", "lotesadminbot"), None, 1)),
    )
    msg = make_message(text="@lotesadminbot", chat=_dm_chat())
    await dm_menu.dm_wl_target(msg, state=fsm, **base_data)
    crud.upsert_scam_entry.assert_not_awaited()
    assert "scam_no_target" in msg.answer.await_args.args[0]
    assert await fsm.get_state() == dm_menu.DmWl.awaiting_target


def _entry(uid: int, source: str, reason: str | None = None):
    return SimpleNamespace(user_id=uid, source=source, reason=reason)


async def test_rt_list_shows_badged_rows(base_data, fsm, monkeypatch):
    entries = [
        _entry(500, SCAM_SOURCE_SCAM, "спам"),
        _entry(600, SCAM_SOURCE_VERIFIED, None),
        _entry(700, "manual", "вручную"),
    ]
    monkeypatch.setattr(crud, "list_scam_entries", AsyncMock(return_value=entries))
    monkeypatch.setattr(
        crud,
        "get_users_by_ids",
        AsyncMock(return_value={500: "Scammer", 600: "Trusted", 700: "Manual"}),
    )
    calls = _capturing_translator(base_data)
    cb = _cb("dm:rt:list")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    text = cb.message.edit_text.await_args.args[0]
    assert "dm_rt_list_title" in text
    items = [v for k, v in calls if k == "dm_rt_list_item"]
    assert len(items) == 3
    assert items[0]["mention"] == '<a href="tg://user?id=500">Scammer</a>'
    assert "dm_rt_badge_scam" in items[0]["source_badge"]
    assert items[0]["reason"] == "спам"
    assert items[1]["source_badge"] == "dm_rt_badge_verified"
    assert items[2]["source_badge"] == "dm_rt_badge_other"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == ["dm:menu"]
    cb.answer.assert_awaited_once()


async def test_rt_list_empty(base_data, fsm, monkeypatch):
    monkeypatch.setattr(crud, "list_scam_entries", AsyncMock(return_value=[]))
    cb = _cb("dm:rt:list")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert cb.message.edit_text.await_args.args[0] == "dm_rt_list_empty"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == ["dm:menu"]


async def test_rt_list_caps_at_15_entries(base_data, fsm, monkeypatch):
    entries = [_entry(1000 + i, SCAM_SOURCE_VERIFIED, None) for i in range(20)]
    monkeypatch.setattr(crud, "list_scam_entries", AsyncMock(return_value=entries))
    monkeypatch.setattr(
        crud,
        "get_users_by_ids",
        AsyncMock(return_value={1000 + i: f"U{i}" for i in range(20)}),
    )
    calls = _capturing_translator(base_data)
    cb = _cb("dm:rt:list")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    items = [v for k, v in calls if k == "dm_rt_list_item"]
    assert len(items) == 15
    assert "tg://user?id=1000" in items[0]["mention"]


# --------------------------------------------------------------------------- #
# Статистика & Топ on the group panel (dm:a:stats:<chat_id> / dm:a:top:<chat_id>)
# --------------------------------------------------------------------------- #
async def test_panel_stats_shows_totals_and_top(base_data, fsm, monkeypatch):
    monkeypatch.setattr(crud, "get_chat", AsyncMock(return_value=_group()))
    monkeypatch.setattr(crud, "chat_activity_totals", AsyncMock(return_value=(100, 5)))
    monkeypatch.setattr(crud, "count_bans", AsyncMock(return_value=3))
    monkeypatch.setattr(crud, "count_warns_chat", AsyncMock(return_value=7))
    monkeypatch.setattr(
        crud, "top_active", AsyncMock(return_value=[(500, 42), (600, 7)])
    )
    monkeypatch.setattr(
        crud, "get_users_by_ids", AsyncMock(return_value={500: "Victim", 600: "Other"})
    )
    calls = _capturing_translator(base_data)
    cb = _cb(f"dm:a:stats:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    crud.top_active.assert_awaited_once_with(
        base_data["session"], GROUP_CHAT_ID, 10  # TOP_DEFAULT
    )
    stats_kwargs = next(v for k, v in calls if k == "dm_stats_text")
    assert stats_kwargs == {
        "title": "Test Group",
        "total": 100,
        "users": 5,
        "banned": 3,
        "warns": 7,
    }
    header_kwargs = next(v for k, v in calls if k == "top_header")
    assert header_kwargs == {"count": 2}
    items = [v for k, v in calls if k == "top_item"]
    assert items[0]["medal"] == "🥇"
    assert "tg://user?id=500" in items[0]["user"]
    assert items[1]["medal"] == "🥈"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    # Back to the group panel + home (no separate stats tab anymore).
    assert [row[0].callback_data for row in kb.inline_keyboard] == [
        f"dm:g:{GROUP_CHAT_ID}",
        "dm:menu",
    ]
    cb.answer.assert_awaited_once()


async def test_panel_stats_top_empty(base_data, fsm, monkeypatch):
    monkeypatch.setattr(crud, "get_chat", AsyncMock(return_value=_group()))
    monkeypatch.setattr(crud, "chat_activity_totals", AsyncMock(return_value=(0, 0)))
    monkeypatch.setattr(crud, "count_bans", AsyncMock(return_value=0))
    monkeypatch.setattr(crud, "count_warns_chat", AsyncMock(return_value=0))
    monkeypatch.setattr(crud, "top_active", AsyncMock(return_value=[]))
    _capturing_translator(base_data)
    cb = _cb(f"dm:a:stats:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    text = cb.message.edit_text.await_args.args[0]
    assert "dm_stats_text" in text
    assert "top_empty" in text
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == [
        f"dm:g:{GROUP_CHAT_ID}",
        "dm:menu",
    ]


async def test_panel_stats_missing_group(base_data, fsm, monkeypatch):
    monkeypatch.setattr(crud, "get_chat", AsyncMock(return_value=None))
    cb = _cb(f"dm:a:stats:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert cb.message.edit_text.await_args.args[0] == "dm_panel_missing"


async def test_panel_top_shows_lines(base_data, fsm, monkeypatch):
    monkeypatch.setattr(crud, "get_chat", AsyncMock(return_value=_group()))
    monkeypatch.setattr(
        crud, "top_active", AsyncMock(return_value=[(500, 42), (600, 7)])
    )
    monkeypatch.setattr(
        crud, "get_users_by_ids", AsyncMock(return_value={500: "Victim", 600: "Other"})
    )
    calls = _capturing_translator(base_data)
    cb = _cb(f"dm:a:top:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    crud.top_active.assert_awaited_once_with(
        base_data["session"], GROUP_CHAT_ID, 10  # TOP_DEFAULT
    )
    crud.chat_activity_totals.assert_not_awaited()  # top-only screen
    header_kwargs = next(v for k, v in calls if k == "top_header")
    assert header_kwargs == {"count": 2}
    items = [v for k, v in calls if k == "top_item"]
    assert items[0]["medal"] == "🥇"
    assert "tg://user?id=500" in items[0]["user"]
    assert items[1]["medal"] == "🥈"
    assert "dm_stats_text" not in [k for k, _v in calls]
    text = cb.message.edit_text.await_args.args[0]
    assert text == "top_header\n" + "\n".join(
        ["top_item", "top_item"]
    )
    assert cb.message.edit_text.await_args.kwargs["parse_mode"] == "HTML"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == [
        f"dm:g:{GROUP_CHAT_ID}",
        "dm:menu",
    ]
    cb.answer.assert_awaited_once()


async def test_panel_top_empty(base_data, fsm, monkeypatch):
    monkeypatch.setattr(crud, "get_chat", AsyncMock(return_value=_group()))
    monkeypatch.setattr(crud, "top_active", AsyncMock(return_value=[]))
    cb = _cb(f"dm:a:top:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert cb.message.edit_text.await_args.args[0] == "top_empty"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == [
        f"dm:g:{GROUP_CHAT_ID}",
        "dm:menu",
    ]


# --------------------------------------------------------------------------- #
# Рассылки tab
# --------------------------------------------------------------------------- #
async def test_tab_broadcast_shows_groups(base_data, fsm, monkeypatch):
    monkeypatch.setattr(crud, "list_active_chats", AsyncMock(return_value=_chats(2)))
    cb = _cb("dm:tab:broadcast")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert cb.message.edit_text.await_args.args[0] == "dm_bc_groups_title"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    callbacks = [row[0].callback_data for row in kb.inline_keyboard]
    assert callbacks == ["dm:bc:1000", "dm:bc:1001", "dm:menu"]
    cb.answer.assert_awaited_once()


async def test_broadcast_topics_checkboxes(base_data, fsm, monkeypatch):
    monkeypatch.setattr(crud, "get_chat", AsyncMock(return_value=_group()))
    monkeypatch.setattr(
        crud, "list_topics", AsyncMock(return_value=[_topic(5), _topic(7)])
    )
    cb = _cb(f"dm:bc:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert cb.message.edit_text.await_args.args[0] == "dm_bc_topics_title"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    rows = kb.inline_keyboard
    assert rows[0][0].callback_data == f"dm:bct:{GROUP_CHAT_ID}:5"
    assert rows[0][0].text == "#5"
    assert rows[0][0].icon_custom_emoji_id == "5352618591961226857"  # ⚪ unchecked
    assert rows[1][0].callback_data == f"dm:bct:{GROUP_CHAT_ID}:7"
    assert rows[2][0].callback_data == f"dm:bcgo:{GROUP_CHAT_ID}"
    assert rows[2][0].text == "dm_bc_go"
    assert rows[2][0].icon_custom_emoji_id == "5197269100878907942"  # ✍
    assert rows[3][0].callback_data == "dm:menu"
    state_data = await fsm.get_data()
    assert state_data["bc_chat_id"] == GROUP_CHAT_ID
    assert state_data["selected"] == []
    cb.answer.assert_awaited_once()


async def test_broadcast_no_topics(base_data, fsm, monkeypatch):
    monkeypatch.setattr(crud, "get_chat", AsyncMock(return_value=_group()))
    monkeypatch.setattr(crud, "list_topics", AsyncMock(return_value=[]))
    cb = _cb(f"dm:bc:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert cb.message.edit_text.await_args.args[0] == "dm_bc_no_topics"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == [
        "dm:tab:broadcast",
        "dm:menu",
    ]


async def test_broadcast_toggle_selects_and_deselects(base_data, fsm, monkeypatch):
    monkeypatch.setattr(
        crud, "list_topics", AsyncMock(return_value=[_topic(5), _topic(7)])
    )
    await fsm.update_data(bc_chat_id=GROUP_CHAT_ID, selected=[])

    cb = _cb(f"dm:bct:{GROUP_CHAT_ID}:5")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert (await fsm.get_data())["selected"] == [5]
    kb = cb.message.edit_reply_markup.await_args.kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].text == "#5"
    assert kb.inline_keyboard[0][0].icon_custom_emoji_id == "5776375003280838798"  # ✅
    cb.answer.assert_awaited_once()

    cb2 = _cb(f"dm:bct:{GROUP_CHAT_ID}:5")
    await dm_menu.on_dm_callback(cb2, state=fsm, **base_data)
    assert (await fsm.get_data())["selected"] == []
    kb2 = cb2.message.edit_reply_markup.await_args.kwargs["reply_markup"]
    assert kb2.inline_keyboard[0][0].text == "#5"
    assert kb2.inline_keyboard[0][0].icon_custom_emoji_id == "5352618591961226857"  # ⚪


async def test_broadcast_go_with_empty_selection_answers_none(base_data, fsm):
    await fsm.update_data(bc_chat_id=GROUP_CHAT_ID, selected=[])
    cb = _cb(f"dm:bcgo:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    cb.answer.assert_awaited_once_with("dm_bc_none")
    assert await fsm.get_state() is None
    cb.message.edit_text.assert_not_awaited()


async def test_broadcast_go_starts_text_fsm(base_data, fsm):
    await fsm.update_data(bc_chat_id=GROUP_CHAT_ID, selected=[5, 7])
    cb = _cb(f"dm:bcgo:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert await fsm.get_state() == dm_menu.DmBroadcast.awaiting_text
    state_data = await fsm.get_data()
    assert state_data["chat_id"] == GROUP_CHAT_ID
    assert state_data["thread_ids"] == [5, 7]
    assert cb.message.edit_text.await_args.args[0] == "dm_bc_text_prompt"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == [
        f"dm:bc:{GROUP_CHAT_ID}",
        "dm:menu",
    ]


async def test_broadcast_message_sends_and_reports(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmBroadcast.awaiting_text)
    await fsm.update_data(chat_id=GROUP_CHAT_ID, thread_ids=[5, 7])
    results = [
        {"thread_id": 5, "ok": True, "error": None},
        {"thread_id": 7, "ok": False, "error": "boom"},
    ]
    monkeypatch.setattr(dm_menu, "send_broadcast", AsyncMock(return_value=results))
    msg = make_message(text="Рассылка!", chat=_dm_chat())
    await dm_menu.dm_broadcast_text(msg, state=fsm, **base_data)
    dm_menu.send_broadcast.assert_awaited_once_with(
        msg.bot, GROUP_CHAT_ID, [5, 7], "Рассылка!"
    )
    assert await fsm.get_state() is None
    text = msg.answer.await_args.args[0]
    assert "dm_bc_result" in text
    assert "#5: ✅" in text
    assert "#7: ❌ boom" in text
    kb = msg.answer.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == ["dm:menu"]


async def test_broadcast_message_empty_keeps_state(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmBroadcast.awaiting_text)
    await fsm.update_data(chat_id=GROUP_CHAT_ID, thread_ids=[5])
    monkeypatch.setattr(dm_menu, "send_broadcast", AsyncMock())
    msg = make_message(text="   ", chat=_dm_chat())
    await dm_menu.dm_broadcast_text(msg, state=fsm, **base_data)
    assert msg.answer.await_args.args[0] == "dm_bc_empty"
    assert await fsm.get_state() == dm_menu.DmBroadcast.awaiting_text
    dm_menu.send_broadcast.assert_not_awaited()


async def test_broadcast_back_to_topics_clears_text_fsm(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmBroadcast.awaiting_text)
    await fsm.update_data(
        chat_id=GROUP_CHAT_ID, thread_ids=[5], bc_chat_id=GROUP_CHAT_ID, selected=[5]
    )
    monkeypatch.setattr(crud, "get_chat", AsyncMock(return_value=_group()))
    monkeypatch.setattr(
        crud, "list_topics", AsyncMock(return_value=[_topic(5), _topic(7)])
    )
    cb = _cb(f"dm:bc:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    # The pending text FSM is dropped; selection is kept for re-picking.
    assert await fsm.get_state() is None
    state_data = await fsm.get_data()
    assert state_data["selected"] == [5]
    assert state_data["bc_chat_id"] == GROUP_CHAT_ID


# --------------------------------------------------------------------------- #
# dm:menu bails out of any FSM
# --------------------------------------------------------------------------- #
async def test_menu_clears_broadcast_fsm(base_data, fsm):
    await fsm.set_state(dm_menu.DmBroadcast.awaiting_text)
    await fsm.update_data(chat_id=GROUP_CHAT_ID, thread_ids=[5])
    cb = _cb("dm:menu")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert await fsm.get_state() is None
    assert await fsm.get_data() == {}
    assert cb.message.edit_text.await_args.args[0] == "dm_menu_title"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    # Row 0 is the WebApp «Панель» button; rows 1-3 are the three tabs.
    assert [row[0].callback_data for row in kb.inline_keyboard[1:4]] == [
        "dm:tab:admin",
        "dm:tab:ratings",
        "dm:tab:broadcast",
    ]


async def test_menu_clears_slow_mode_fsm(base_data, fsm):
    await fsm.set_state(dm_menu.DmSlowMode.awaiting_config)
    await fsm.update_data(chat_id=GROUP_CHAT_ID)
    cb = _cb("dm:menu")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert await fsm.get_state() is None
    assert await fsm.get_data() == {}
