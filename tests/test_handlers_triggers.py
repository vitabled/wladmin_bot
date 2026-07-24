"""Handler tests for trigger auto-replies and their settings commands."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bot.db import crud
from bot.handlers import settings_cmd, triggers
from tests.conftest import Cmd, make_message, make_user


# --------------------------------------------------------------------------- #
# Auto-reply guard
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def patch_send(monkeypatch):
    monkeypatch.setattr(triggers, "safe_send_message", AsyncMock(return_value=None))


async def test_reply_sent_on_match(base_data):
    base_data["settings"]["triggers_enabled"] = True
    base_data["redis"].get_cached_triggers = AsyncMock(
        return_value=[{"pattern": "hi", "match_type": "contains", "reply_text": "Yo"}]
    )
    msg = make_message(text="hi everyone", from_user=make_user(7, "U"))
    assert await triggers.maybe_reply(msg, base_data) is True
    triggers.safe_send_message.assert_awaited_once()


async def test_no_reply_when_disabled(base_data):
    base_data["settings"]["triggers_enabled"] = False
    msg = make_message(text="hi", from_user=make_user(7, "U"))
    assert await triggers.maybe_reply(msg, base_data) is False
    triggers.safe_send_message.assert_not_awaited()


async def test_no_reply_without_match(base_data):
    base_data["settings"]["triggers_enabled"] = True
    base_data["redis"].get_cached_triggers = AsyncMock(
        return_value=[{"pattern": "buy", "match_type": "contains", "reply_text": "No"}]
    )
    msg = make_message(text="nice weather", from_user=make_user(7, "U"))
    assert await triggers.maybe_reply(msg, base_data) is False


async def test_cache_miss_loads_from_db(base_data, monkeypatch):
    base_data["settings"]["triggers_enabled"] = True
    base_data["redis"].get_cached_triggers = AsyncMock(return_value=None)
    base_data["redis"].cache_triggers = AsyncMock()
    monkeypatch.setattr(
        crud,
        "list_triggers",
        AsyncMock(
            return_value=[
                {"pattern": "ping", "match_type": "exact", "reply_text": "pong"}
            ]
        ),
    )
    msg = make_message(text="ping", from_user=make_user(7, "U"))
    assert await triggers.maybe_reply(msg, base_data) is True
    base_data["redis"].cache_triggers.assert_awaited_once()


async def test_sender_chat_skipped(base_data):
    base_data["settings"]["triggers_enabled"] = True
    msg = make_message(text="hi", sender_chat=object())
    assert await triggers.maybe_reply(msg, base_data) is False


# --------------------------------------------------------------------------- #
# Settings commands
# --------------------------------------------------------------------------- #
@pytest.fixture
def patch_crud(monkeypatch):
    monkeypatch.setattr(settings_cmd.crud, "update_settings", AsyncMock())
    monkeypatch.setattr(settings_cmd.crud, "count_triggers", AsyncMock(return_value=0))
    monkeypatch.setattr(settings_cmd.crud, "add_trigger", AsyncMock(return_value=True))
    monkeypatch.setattr(
        settings_cmd.crud, "remove_trigger", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(settings_cmd.crud, "list_triggers", AsyncMock(return_value=[]))


async def test_addtrigger_ok(base_data, patch_crud):
    msg = make_message()
    await settings_cmd.cmd_addtrigger(msg, Cmd("hello | Hi there!"), **base_data)
    args, _ = settings_cmd.crud.add_trigger.await_args
    # (session, chat_id, pattern, reply_text)
    assert args[2] == "hello"
    assert args[3] == "Hi there!"
    settings_cmd.crud.update_settings.assert_awaited()  # auto-enabled


async def test_addtrigger_missing_separator(base_data, patch_crud):
    msg = make_message()
    await settings_cmd.cmd_addtrigger(msg, Cmd("no separator here"), **base_data)
    settings_cmd.crud.add_trigger.assert_not_awaited()
    msg.reply.assert_awaited_once()


async def test_addtrigger_respects_limit(base_data, patch_crud, monkeypatch):
    monkeypatch.setattr(
        settings_cmd.crud, "count_triggers", AsyncMock(return_value=1000)
    )
    msg = make_message()
    await settings_cmd.cmd_addtrigger(msg, Cmd("a | b"), **base_data)
    settings_cmd.crud.add_trigger.assert_not_awaited()


async def test_deltrigger(base_data, patch_crud):
    msg = make_message()
    await settings_cmd.cmd_deltrigger(msg, Cmd("hello"), **base_data)
    settings_cmd.crud.remove_trigger.assert_awaited_once()


async def test_triggers_toggle_off(base_data, patch_crud):
    msg = make_message()
    await settings_cmd.cmd_triggers(msg, Cmd("off"), **base_data)
    _, kwargs = settings_cmd.crud.update_settings.await_args
    assert kwargs == {"triggers_enabled": False}


async def test_triggers_list_empty(base_data, patch_crud):
    msg = make_message()
    await settings_cmd.cmd_triggers(msg, Cmd(None), **base_data)
    settings_cmd.crud.list_triggers.assert_awaited_once()
    msg.reply.assert_awaited_once()


async def test_non_admin_blocked(base_data, patch_crud):
    base_data["is_admin"] = False
    msg = make_message()
    await settings_cmd.cmd_addtrigger(msg, Cmd("a | b"), **base_data)
    settings_cmd.crud.add_trigger.assert_not_awaited()
