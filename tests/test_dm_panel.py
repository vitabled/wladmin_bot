"""Handler tests for the DM per-group admin panel (bot/handlers/dm_menu.py).

Covers the «👥 Группы» flow: groups list + pagination, the per-group panel
keyboard, moderation actions through the ``DmAdmin`` FSM (target awaiting),
whitelist add/remove, scam scoped to the SELECTED group, settings toggles via
``dm:set`` and stats/top — every action operating on the chosen ``chat_id``.

Follows the established direct-handler style: mocked messages/callbacks from
tests/conftest.py and a real FSMContext backed by MemoryStorage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.constants import SCAM_SOURCE_VERIFIED
from bot.db import crud
from bot.handlers import dm_menu
from bot.utils.targets import Target
from tests.conftest import (
    DEFAULT_SETTINGS,
    make_callback,
    make_chat,
    make_message,
    make_user,
)

GROUP_CHAT_ID = 12345


def _translate(key, **kwargs):
    return key


def _dm_chat():
    return make_chat(111, "private", "PM")


@pytest.fixture(autouse=True)
def patch_crud(monkeypatch):
    """No DB in unit tests: every panel crud call is mocked by default."""
    for name in (
        "list_active_chats",
        "get_chat",
        "get_or_create_settings",
        "update_settings",
        "get_activity",
        "chat_activity_totals",
        "top_active",
        "get_users_by_ids",
        "get_scam_entry",
        "get_user_by_username",
        "upsert_scam_entry",
        "remove_scam_entry",
        "deactivate_last_warn",
        "count_active_warns",
        "list_active_warns",
        "add_mod_log",
        "get_slow_mode",
        "set_slow_mode",
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


def _capturing_translator(base_data):
    """Replace ``_`` with one that records (key, kwargs) for assertions."""
    calls: list[tuple[str, dict]] = []

    def t(key, **kwargs):
        calls.append((key, kwargs))
        return key

    base_data["_"] = t
    return calls


# --------------------------------------------------------------------------- #
# Groups list (dm:groups / dm:gp:N)
# --------------------------------------------------------------------------- #
async def test_groups_lists_groups(base_data, monkeypatch):
    monkeypatch.setattr(crud, "list_active_chats", AsyncMock(return_value=_chats(2)))
    cb = _cb("dm:groups")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert cb.message.edit_text.await_args.args[0] == "dm_groups_title"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    callbacks = [row[0].callback_data for row in kb.inline_keyboard]
    assert callbacks == ["dm:g:1000", "dm:g:1001", "dm:menu"]
    assert kb.inline_keyboard[0][0].text == "Group 0"
    cb.answer.assert_awaited_once()


async def test_groups_pagination(base_data, monkeypatch):
    monkeypatch.setattr(crud, "list_active_chats", AsyncMock(return_value=_chats(10)))
    cb = _cb("dm:groups")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    callbacks = [row[0].callback_data for row in kb.inline_keyboard]
    assert callbacks[:8] == [f"dm:g:{1000 + i}" for i in range(8)]
    assert callbacks[8] == "dm:gp:1"  # next page
    assert callbacks[9] == "dm:menu"

    cb2 = _cb("dm:gp:1")
    await dm_menu.on_dm_callback(cb2, state=fsm, **base_data)
    kb2 = cb2.message.edit_text.await_args.kwargs["reply_markup"]
    callbacks2 = [row[0].callback_data for row in kb2.inline_keyboard]
    assert callbacks2[:2] == ["dm:g:1008", "dm:g:1009"]
    assert callbacks2[2] == "dm:gp:0"  # prev page
    assert callbacks2[3] == "dm:menu"


async def test_groups_page_clamps_out_of_range(base_data, monkeypatch):
    monkeypatch.setattr(crud, "list_active_chats", AsyncMock(return_value=_chats(10)))
    cb = _cb("dm:gp:99")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].callback_data == "dm:g:1008"  # last page


async def test_groups_empty(base_data, monkeypatch):
    monkeypatch.setattr(crud, "list_active_chats", AsyncMock(return_value=[]))
    cb = _cb("dm:groups")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert cb.message.edit_text.await_args.args[0] == "dm_groups_empty"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == ["dm:menu"]


# --------------------------------------------------------------------------- #
# Group panel (dm:g:<chat_id>)
# --------------------------------------------------------------------------- #
async def test_group_panel_shows_all_actions(base_data, monkeypatch):
    monkeypatch.setattr(crud, "get_chat", AsyncMock(return_value=_group()))
    calls = _capturing_translator(base_data)
    cb = _cb(f"dm:g:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    title_kwargs = next(v for k, v in calls if k == "dm_panel_title")
    assert title_kwargs == {"title": "Test Group", "chat_id": GROUP_CHAT_ID}

    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    callbacks = [
        btn.callback_data for row in kb.inline_keyboard for btn in row
    ]
    expected = [
        f"dm:a:{act}:{GROUP_CHAT_ID}"
        for act in (
            "ban",
            "kick",
            "mute",
            "warn",
            "unban",
            "unmute",
            "warns",
            "unwarn",
            "settings",
        )
    ] + [
        f"dm:sm:{GROUP_CHAT_ID}",
        f"dm:a:stats:{GROUP_CHAT_ID}",
        f"dm:a:top:{GROUP_CHAT_ID}",
        "dm:groups",
        "dm:menu",
    ]
    assert callbacks == expected
    # scam / wl moved to the Рейтинги tab; stats/top live on the panel.
    assert "dm:a:scam" not in callbacks
    assert "dm:a:wl" not in callbacks
    assert "dm:a:wl_remove" not in callbacks
    # 7 rows of 2 buttons: 8 mod actions + settings|slow-mode + stats|top
    # + groups|home.
    assert [len(row) for row in kb.inline_keyboard] == [2, 2, 2, 2, 2, 2, 2]
    cb.answer.assert_awaited_once()


async def test_group_panel_missing_chat(base_data, monkeypatch):
    monkeypatch.setattr(crud, "get_chat", AsyncMock(return_value=None))
    cb = _cb("dm:g:99999")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert cb.message.edit_text.await_args.args[0] == "dm_panel_missing"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == ["dm:menu"]


async def test_group_panel_empty_title_falls_back_to_id(base_data, monkeypatch):
    monkeypatch.setattr(
        crud, "get_chat", AsyncMock(return_value=SimpleNamespace(chat_id=7, title=""))
    )
    calls = _capturing_translator(base_data)
    cb = _cb("dm:g:7")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    title_kwargs = next(v for k, v in calls if k == "dm_panel_title")
    assert title_kwargs == {"title": "7", "chat_id": 7}


# --------------------------------------------------------------------------- #
# Target-requiring actions: FSM setup (dm:a:<action>:<chat_id>)
# --------------------------------------------------------------------------- #
async def test_panel_ban_sets_fsm_with_duration_prompt(base_data, fsm):
    cb = _cb(f"dm:a:ban:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert await fsm.get_state() == dm_menu.DmAdmin.awaiting_target
    state_data = await fsm.get_data()
    assert state_data["action"] == "ban"
    assert state_data["chat_id"] == GROUP_CHAT_ID
    assert cb.message.edit_text.await_args.args[0] == "dm_action_prompt_duration"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == [
        f"dm:g:{GROUP_CHAT_ID}",
        "dm:menu",
    ]
    cb.answer.assert_awaited_once()


async def test_panel_kick_prompt_without_duration(base_data, fsm):
    cb = _cb(f"dm:a:kick:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert await fsm.get_state() == dm_menu.DmAdmin.awaiting_target
    assert cb.message.edit_text.await_args.args[0] == "dm_action_prompt"


async def test_panel_wl_remove_sets_fsm(base_data, fsm):
    cb = _cb(f"dm:a:wl_remove:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    state_data = await fsm.get_data()
    assert state_data["action"] == "wl_remove"
    assert state_data["chat_id"] == GROUP_CHAT_ID


# --------------------------------------------------------------------------- #
# DmAdmin.awaiting_target — moderation actions
# --------------------------------------------------------------------------- #
def _prepared(user_id: int = 500, duration: int | None = None, reason: str | None = None):
    from bot.handlers.moderation import Prepared

    return Prepared(
        target=Target(user_id, "Victim", "victim"), duration=duration, reason=reason
    )


async def test_admin_target_ban_applies(base_data, fsm, monkeypatch):
    from bot.handlers import actions

    await fsm.set_state(dm_menu.DmAdmin.awaiting_target)
    await fsm.update_data(action="ban", chat_id=GROUP_CHAT_ID)
    monkeypatch.setattr(dm_menu, "prepare_action", AsyncMock(return_value=_prepared()))
    monkeypatch.setattr(actions, "do_ban", AsyncMock(return_value=True))

    msg = make_message(text="@victim", chat=_dm_chat())
    await dm_menu.dm_admin_target(msg, state=fsm, **base_data)

    call_kwargs = dm_menu.prepare_action.await_args.kwargs
    assert call_kwargs["chat_id"] == GROUP_CHAT_ID
    assert call_kwargs["allow_duration"] is True
    assert call_kwargs["protect_target"] is True
    assert call_kwargs["need_restrict"] is True
    actions.do_ban.assert_awaited_once_with(
        msg.bot, base_data["session"], GROUP_CHAT_ID, 1000, 500, None, None
    )
    text = msg.answer.await_args.args[0]
    assert "mod_ban" in text
    assert msg.answer.await_args.kwargs["parse_mode"] == "HTML"
    kb = msg.answer.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == [
        f"dm:g:{GROUP_CHAT_ID}",
        "dm:menu",
    ]
    assert await fsm.get_state() is None  # cleared after the action


async def test_admin_target_ban_temp_with_reason(base_data, fsm, monkeypatch):
    from bot.handlers import actions

    await fsm.set_state(dm_menu.DmAdmin.awaiting_target)
    await fsm.update_data(action="ban", chat_id=GROUP_CHAT_ID)
    monkeypatch.setattr(
        dm_menu, "prepare_action", AsyncMock(return_value=_prepared(duration=7200, reason="спам"))
    )
    monkeypatch.setattr(actions, "do_ban", AsyncMock(return_value=True))
    msg = make_message(text="@victim 2h спам", chat=_dm_chat())
    await dm_menu.dm_admin_target(msg, state=fsm, **base_data)
    actions.do_ban.assert_awaited_once_with(
        msg.bot, base_data["session"], GROUP_CHAT_ID, 1000, 500, 7200, "спам"
    )
    assert "mod_ban_temp" in msg.answer.await_args.args[0]


async def test_admin_target_ban_bot_lacks_rights(base_data, fsm, monkeypatch):
    from bot.handlers import actions

    await fsm.set_state(dm_menu.DmAdmin.awaiting_target)
    await fsm.update_data(action="ban", chat_id=GROUP_CHAT_ID)
    monkeypatch.setattr(dm_menu, "prepare_action", AsyncMock(return_value=_prepared()))
    monkeypatch.setattr(actions, "do_ban", AsyncMock(return_value=False))
    msg = make_message(text="@victim", chat=_dm_chat())
    await dm_menu.dm_admin_target(msg, state=fsm, **base_data)
    assert msg.answer.await_args.args[0] == "error_bot_not_admin"
    assert await fsm.get_state() is None


async def test_admin_target_invalid_keeps_state(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmAdmin.awaiting_target)
    await fsm.update_data(action="ban", chat_id=GROUP_CHAT_ID)
    monkeypatch.setattr(dm_menu, "prepare_action", AsyncMock(return_value=None))
    msg = make_message(text="not-a-target", chat=_dm_chat())
    await dm_menu.dm_admin_target(msg, state=fsm, **base_data)
    assert await fsm.get_state() == dm_menu.DmAdmin.awaiting_target
    msg.answer.assert_not_awaited()


async def test_admin_target_warn_applies_with_group_settings(
    base_data, fsm, monkeypatch
):
    from bot.handlers import actions

    await fsm.set_state(dm_menu.DmAdmin.awaiting_target)
    await fsm.update_data(action="warn", chat_id=GROUP_CHAT_ID)
    monkeypatch.setattr(
        dm_menu, "prepare_action", AsyncMock(return_value=_prepared(reason="спам"))
    )
    monkeypatch.setattr(
        crud,
        "get_or_create_settings",
        AsyncMock(return_value=SimpleNamespace(**DEFAULT_SETTINGS)),
    )
    monkeypatch.setattr(
        actions,
        "do_warn",
        AsyncMock(return_value=SimpleNamespace(count=1, limit=3, action_applied=None)),
    )
    msg = make_message(text="@victim спам", chat=_dm_chat())
    await dm_menu.dm_admin_target(msg, state=fsm, **base_data)

    call_kwargs = dm_menu.prepare_action.await_args.kwargs
    assert call_kwargs["allow_duration"] is False
    assert call_kwargs["protect_target"] is True
    assert call_kwargs["need_restrict"] is True
    # warn limit came from the SELECTED group's settings, not the DM
    do_warn_args = actions.do_warn.await_args.args
    assert do_warn_args[2] == GROUP_CHAT_ID
    assert do_warn_args[6]["warn_limit"] == 3
    assert "mod_warn" in msg.answer.await_args.args[0]
    assert await fsm.get_state() is None


async def test_admin_target_unwarn(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmAdmin.awaiting_target)
    await fsm.update_data(action="unwarn", chat_id=GROUP_CHAT_ID)
    monkeypatch.setattr(dm_menu, "prepare_action", AsyncMock(return_value=_prepared()))
    monkeypatch.setattr(crud, "deactivate_last_warn", AsyncMock(return_value=True))
    monkeypatch.setattr(crud, "count_active_warns", AsyncMock(return_value=2))
    msg = make_message(text="@victim", chat=_dm_chat())
    await dm_menu.dm_admin_target(msg, state=fsm, **base_data)

    call_kwargs = dm_menu.prepare_action.await_args.kwargs
    assert call_kwargs["protect_target"] is False
    assert call_kwargs["need_restrict"] is False
    crud.deactivate_last_warn.assert_awaited_once_with(
        base_data["session"], GROUP_CHAT_ID, 500
    )
    crud.add_mod_log.assert_awaited_once()
    assert "mod_unwarn" in msg.answer.await_args.args[0]
    assert await fsm.get_state() is None


async def test_admin_target_unwarn_none(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmAdmin.awaiting_target)
    await fsm.update_data(action="unwarn", chat_id=GROUP_CHAT_ID)
    monkeypatch.setattr(dm_menu, "prepare_action", AsyncMock(return_value=_prepared()))
    monkeypatch.setattr(crud, "deactivate_last_warn", AsyncMock(return_value=False))
    msg = make_message(text="@victim", chat=_dm_chat())
    await dm_menu.dm_admin_target(msg, state=fsm, **base_data)
    assert "mod_unwarn_none" in msg.answer.await_args.args[0]


async def test_admin_target_warns_lists(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmAdmin.awaiting_target)
    await fsm.update_data(action="warns", chat_id=GROUP_CHAT_ID)
    monkeypatch.setattr(dm_menu, "prepare_action", AsyncMock(return_value=_prepared()))
    monkeypatch.setattr(
        crud,
        "get_or_create_settings",
        AsyncMock(return_value=SimpleNamespace(**DEFAULT_SETTINGS)),
    )
    monkeypatch.setattr(
        crud,
        "list_active_warns",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    reason="спам", created_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
                )
            ]
        ),
    )
    msg = make_message(text="@victim", chat=_dm_chat())
    await dm_menu.dm_admin_target(msg, state=fsm, **base_data)

    call_kwargs = dm_menu.prepare_action.await_args.kwargs
    assert call_kwargs["allow_duration"] is False
    assert call_kwargs["protect_target"] is False
    assert call_kwargs["need_restrict"] is False
    text = msg.answer.await_args.args[0]
    assert "mod_warns_header" in text
    assert "mod_warns_item" in text
    assert await fsm.get_state() is None


# --------------------------------------------------------------------------- #
# DmAdmin.awaiting_target — whitelist add/remove
# --------------------------------------------------------------------------- #
async def test_admin_target_wl_adds(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmAdmin.awaiting_target)
    await fsm.update_data(action="wl", chat_id=GROUP_CHAT_ID)
    monkeypatch.setattr(
        dm_menu,
        "resolve_target",
        AsyncMock(return_value=(Target(500, "Victim", "victim"), None, 1)),
    )
    msg = make_message(text="@victim", chat=_dm_chat())
    await dm_menu.dm_admin_target(msg, state=fsm, **base_data)

    crud.upsert_scam_entry.assert_awaited_once_with(
        base_data["session"], 500, SCAM_SOURCE_VERIFIED, None
    )
    text = msg.answer.await_args.args[0]
    assert "addtowl_added" in text
    kb = msg.answer.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == [
        f"dm:g:{GROUP_CHAT_ID}",
        "dm:menu",
    ]
    assert await fsm.get_state() is None


async def test_admin_target_wl_remove(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmAdmin.awaiting_target)
    await fsm.update_data(action="wl_remove", chat_id=GROUP_CHAT_ID)
    monkeypatch.setattr(
        dm_menu,
        "resolve_target",
        AsyncMock(return_value=(Target(500, "Victim", "victim"), None, 1)),
    )
    monkeypatch.setattr(crud, "remove_scam_entry", AsyncMock(return_value=True))
    msg = make_message(text="@victim", chat=_dm_chat())
    await dm_menu.dm_admin_target(msg, state=fsm, **base_data)
    crud.remove_scam_entry.assert_awaited_once_with(base_data["session"], 500)
    assert "addtowl_removed" in msg.answer.await_args.args[0]
    assert await fsm.get_state() is None


async def test_admin_target_wl_remove_not_found(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmAdmin.awaiting_target)
    await fsm.update_data(action="wl_remove", chat_id=GROUP_CHAT_ID)
    monkeypatch.setattr(
        dm_menu,
        "resolve_target",
        AsyncMock(return_value=(Target(500, "Victim", "victim"), None, 1)),
    )
    monkeypatch.setattr(crud, "remove_scam_entry", AsyncMock(return_value=False))
    msg = make_message(text="@victim", chat=_dm_chat())
    await dm_menu.dm_admin_target(msg, state=fsm, **base_data)
    assert "addtowl_not_found" in msg.answer.await_args.args[0]
    assert await fsm.get_state() is None


async def test_admin_target_wl_invalid_keeps_state(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmAdmin.awaiting_target)
    await fsm.update_data(action="wl", chat_id=GROUP_CHAT_ID)
    monkeypatch.setattr(
        dm_menu, "resolve_target", AsyncMock(return_value=(None, "error_no_target", 0))
    )
    msg = make_message(text="not-a-target", chat=_dm_chat())
    await dm_menu.dm_admin_target(msg, state=fsm, **base_data)
    text = msg.answer.await_args.args[0]
    assert "scam_no_target" in text
    kb = msg.answer.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == [
        f"dm:g:{GROUP_CHAT_ID}",
        "dm:menu",
    ]
    assert await fsm.get_state() == dm_menu.DmAdmin.awaiting_target


async def test_admin_target_wl_bot_username_keeps_state(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmAdmin.awaiting_target)
    await fsm.update_data(action="wl", chat_id=GROUP_CHAT_ID)
    monkeypatch.setattr(
        dm_menu,
        "resolve_target",
        AsyncMock(return_value=(Target(42, "Bot", "lotesadminbot"), None, 1)),
    )
    msg = make_message(text="@lotesadminbot", chat=_dm_chat())
    await dm_menu.dm_admin_target(msg, state=fsm, **base_data)
    crud.upsert_scam_entry.assert_not_awaited()
    assert "scam_no_target" in msg.answer.await_args.args[0]
    assert await fsm.get_state() == dm_menu.DmAdmin.awaiting_target


# --------------------------------------------------------------------------- #
# Slow mode (dm:sm:<chat_id>)
# --------------------------------------------------------------------------- #
async def test_panel_slowmode_sets_fsm(base_data, fsm, monkeypatch):
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=None))
    cb = _cb(f"dm:sm:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert await fsm.get_state() == dm_menu.DmSlowMode.awaiting_config
    state_data = await fsm.get_data()
    assert state_data["chat_id"] == GROUP_CHAT_ID
    assert cb.message.edit_text.await_args.args[0] == "dm_sm_prompt"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == [
        f"dm:g:{GROUP_CHAT_ID}",
        "dm:menu",
    ]
    cb.answer.assert_awaited_once()


async def test_panel_slowmode_prompt_shows_current_config(base_data, fsm, monkeypatch):
    monkeypatch.setattr(
        crud,
        "get_slow_mode",
        AsyncMock(
            return_value=SimpleNamespace(
                enabled=True, regular_seconds=21600, wl_seconds=10800, topic_ids=None
            )
        ),
    )
    calls = _capturing_translator(base_data)
    cb = _cb(f"dm:sm:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    on_kwargs = next(v for k, v in calls if k == "dm_sm_current_on")
    assert on_kwargs == {
        "regular": 6,
        "wl": 3,
        "topics": "dm_sm_topics_summary_all",
    }
    prompt_kwargs = next(v for k, v in calls if k == "dm_sm_prompt")
    assert prompt_kwargs == {"current": "dm_sm_current_on"}


async def test_dm_scam_target_from_panel_uses_risk_chat(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmScam.awaiting_target)
    await fsm.update_data(chat_id=GROUP_CHAT_ID)
    fake_verdict = AsyncMock(return_value="verdict_body")
    monkeypatch.setattr(dm_menu, "build_scam_verdict", fake_verdict)

    msg = make_message(text="@someuser", chat=_dm_chat())
    msg.bot.get_chat = AsyncMock(
        return_value=SimpleNamespace(
            id=777, type="private", full_name="Seller", username="someuser"
        )
    )
    await dm_menu.dm_scam_target(msg, state=fsm, **base_data)

    fake_verdict.assert_awaited_once()
    risk_chat = fake_verdict.await_args.kwargs["risk_chat"]
    assert risk_chat is not None
    assert risk_chat.id == GROUP_CHAT_ID
    assert risk_chat.type == "supergroup"
    text = msg.answer.await_args.args[0]
    assert text == "verdict_body\n\nscam_footer"
    kb = msg.answer.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == [
        f"dm:g:{GROUP_CHAT_ID}",
        "dm:menu",
    ]
    assert await fsm.get_state() is None


async def test_dm_scam_target_from_panel_error_keeps_state(base_data, fsm, monkeypatch):
    await fsm.set_state(dm_menu.DmScam.awaiting_target)
    await fsm.update_data(chat_id=GROUP_CHAT_ID)
    monkeypatch.setattr(
        dm_menu, "resolve_target", AsyncMock(return_value=(None, "error_no_target", 0))
    )
    msg = make_message(text="not-a-target", chat=_dm_chat())
    await dm_menu.dm_scam_target(msg, state=fsm, **base_data)
    assert "scam_no_target" in msg.answer.await_args.args[0]
    kb = msg.answer.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == [
        f"dm:g:{GROUP_CHAT_ID}",
        "dm:menu",
    ]
    assert await fsm.get_state() == dm_menu.DmScam.awaiting_target


# --------------------------------------------------------------------------- #
# Settings (dm:a:settings:<chat_id> / dm:set:<chat_id>:<field>)
# --------------------------------------------------------------------------- #
async def test_settings_panel_shows_build_menu_with_dm_callbacks(
    base_data, monkeypatch
):
    monkeypatch.setattr(crud, "get_chat", AsyncMock(return_value=_group()))
    monkeypatch.setattr(
        crud,
        "get_or_create_settings",
        AsyncMock(return_value=SimpleNamespace(**DEFAULT_SETTINGS)),
    )
    cb = _cb(f"dm:a:settings:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    assert cb.message.edit_text.await_args.args[0] == "dm_settings_title"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    rows = kb.inline_keyboard
    # 9 toggles + panel back + home — no close button anywhere.
    assert len(rows) == 11
    # toggle callbacks rewired from menu:t:<field> to dm:set:<chat_id>:<field>
    assert rows[0][0].callback_data == f"dm:set:{GROUP_CHAT_ID}:welcome_enabled"
    assert rows[0][0].text == "menu_welcome"  # state shown via icon
    assert rows[0][0].icon_custom_emoji_id == "5776375003280838798"  # ✅ enabled
    all_callbacks = [
        btn.callback_data for row in rows for btn in row
    ]
    assert "dm:close" not in all_callbacks
    assert "menu:close" not in all_callbacks
    assert rows[-2][0].callback_data == f"dm:g:{GROUP_CHAT_ID}"
    assert rows[-1][0].callback_data == "dm:menu"
    cb.answer.assert_awaited_once()


async def test_settings_toggle_updates_group_and_invalidates(base_data, monkeypatch):
    monkeypatch.setattr(
        crud,
        "get_or_create_settings",
        AsyncMock(return_value=SimpleNamespace(**DEFAULT_SETTINGS)),
    )
    cb = _cb(f"dm:set:{GROUP_CHAT_ID}:filter_links")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)

    args, kwargs = crud.update_settings.await_args
    assert args[1] == GROUP_CHAT_ID
    assert kwargs == {"filter_links": True}  # DEFAULT_SETTINGS has it False
    base_data["redis"].invalidate_settings.assert_awaited_once_with(GROUP_CHAT_ID)
    cb.message.edit_reply_markup.assert_awaited_once()
    cb.answer.assert_awaited_once_with("menu_saved")


async def test_settings_toggle_unknown_field_ignored(base_data, monkeypatch):
    cb = _cb(f"dm:set:{GROUP_CHAT_ID}:bogus")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    cb.answer.assert_awaited_once()
    crud.update_settings.assert_not_awaited()


async def test_settings_missing_chat(base_data, monkeypatch):
    monkeypatch.setattr(crud, "get_chat", AsyncMock(return_value=None))
    cb = _cb(f"dm:a:settings:{GROUP_CHAT_ID}")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert cb.message.edit_text.await_args.args[0] == "dm_panel_missing"


# --------------------------------------------------------------------------- #
# Menu button bails out of the admin FSM
# --------------------------------------------------------------------------- #
async def test_menu_callback_clears_admin_fsm(base_data, fsm):
    await fsm.set_state(dm_menu.DmAdmin.awaiting_target)
    await fsm.update_data(action="ban", chat_id=GROUP_CHAT_ID)
    cb = _cb("dm:menu")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert await fsm.get_state() is None
    assert cb.message.edit_text.await_args.args[0] == "dm_menu_title"
