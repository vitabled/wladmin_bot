"""Handler tests for anti-flood, newbie-media guards and their settings commands."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bot.db import crud
from bot.handlers import actions, antiflood, settings_cmd
from tests.conftest import Cmd, make_message, make_user


@pytest.fixture(autouse=True)
def patch_guards(monkeypatch):
    monkeypatch.setattr(antiflood, "safe_delete_message", AsyncMock(return_value=True))
    monkeypatch.setattr(crud, "add_mod_log", AsyncMock())
    for name in ("do_ban", "do_kick", "do_mute"):
        monkeypatch.setattr(actions, name, AsyncMock())


# --------------------------------------------------------------------------- #
# Newbie media
# --------------------------------------------------------------------------- #
async def test_newbie_media_deleted(base_data):
    base_data["settings"]["newbie_media_enabled"] = True
    base_data["redis"].is_newbie = AsyncMock(return_value=True)
    msg = make_message(content_type="photo", from_user=make_user(500, "N"))
    assert await antiflood.enforce_newbie_media(msg, base_data) is True
    antiflood.safe_delete_message.assert_awaited_once()
    crud.add_mod_log.assert_awaited()


async def test_newbie_media_disabled(base_data):
    base_data["settings"]["newbie_media_enabled"] = False
    msg = make_message(content_type="photo", from_user=make_user(500, "N"))
    assert await antiflood.enforce_newbie_media(msg, base_data) is False
    antiflood.safe_delete_message.assert_not_awaited()


async def test_newbie_text_allowed(base_data):
    base_data["settings"]["newbie_media_enabled"] = True
    base_data["redis"].is_newbie = AsyncMock(return_value=True)
    msg = make_message(content_type="text", from_user=make_user(500, "N"))
    assert await antiflood.enforce_newbie_media(msg, base_data) is False
    antiflood.safe_delete_message.assert_not_awaited()


async def test_established_user_media_allowed(base_data):
    base_data["settings"]["newbie_media_enabled"] = True
    base_data["redis"].is_newbie = AsyncMock(return_value=False)
    msg = make_message(content_type="photo", from_user=make_user(500, "N"))
    assert await antiflood.enforce_newbie_media(msg, base_data) is False
    antiflood.safe_delete_message.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Anti-flood
# --------------------------------------------------------------------------- #
async def test_flood_below_limit_no_action(base_data):
    base_data["settings"]["antiflood_enabled"] = True
    base_data["settings"]["antiflood_limit"] = 5
    base_data["redis"].bump_flood = AsyncMock(return_value=3)
    msg = make_message(text="hi", from_user=make_user(500, "N"))
    assert await antiflood.enforce_flood(msg, base_data) is False
    antiflood.safe_delete_message.assert_not_awaited()


async def test_flood_trips_mute(base_data):
    base_data["settings"]["antiflood_enabled"] = True
    base_data["settings"]["antiflood_limit"] = 5
    base_data["settings"]["antiflood_action"] = "mute"
    base_data["redis"].bump_flood = AsyncMock(return_value=5)
    base_data["redis"].reset_flood = AsyncMock()
    msg = make_message(text="spam", from_user=make_user(500, "N"))
    assert await antiflood.enforce_flood(msg, base_data) is True
    antiflood.safe_delete_message.assert_awaited_once()
    actions.do_mute.assert_awaited_once()
    base_data["redis"].reset_flood.assert_awaited_once()


async def test_flood_trips_ban(base_data):
    base_data["settings"]["antiflood_enabled"] = True
    base_data["settings"]["antiflood_action"] = "ban"
    base_data["redis"].bump_flood = AsyncMock(return_value=9)
    base_data["redis"].reset_flood = AsyncMock()
    msg = make_message(text="spam", from_user=make_user(500, "N"))
    assert await antiflood.enforce_flood(msg, base_data) is True
    actions.do_ban.assert_awaited_once()


async def test_flood_disabled(base_data):
    base_data["settings"]["antiflood_enabled"] = False
    msg = make_message(text="hi", from_user=make_user(500, "N"))
    assert await antiflood.enforce_flood(msg, base_data) is False


# --------------------------------------------------------------------------- #
# Settings commands
# --------------------------------------------------------------------------- #
@pytest.fixture
def patch_save(monkeypatch):
    monkeypatch.setattr(settings_cmd.crud, "update_settings", AsyncMock())


async def test_cmd_antiflood_on(base_data, patch_save):
    msg = make_message()
    await settings_cmd.cmd_antiflood(msg, Cmd("on"), **base_data)
    _, kwargs = settings_cmd.crud.update_settings.await_args
    assert kwargs == {"antiflood_enabled": True}


async def test_cmd_antiflood_config(base_data, patch_save):
    msg = make_message()
    await settings_cmd.cmd_antiflood(msg, Cmd("5 10 ban"), **base_data)
    _, kwargs = settings_cmd.crud.update_settings.await_args
    assert kwargs["antiflood_limit"] == 5
    assert kwargs["antiflood_window"] == 10
    assert kwargs["antiflood_action"] == "ban"
    assert kwargs["antiflood_enabled"] is True


async def test_cmd_antiflood_rejects_bad_window(base_data, patch_save):
    msg = make_message()
    await settings_cmd.cmd_antiflood(msg, Cmd("5 0"), **base_data)
    settings_cmd.crud.update_settings.assert_not_awaited()
    msg.reply.assert_awaited_once()


async def test_cmd_antiflood_rejects_bad_action(base_data, patch_save):
    msg = make_message()
    await settings_cmd.cmd_antiflood(msg, Cmd("5 10 explode"), **base_data)
    settings_cmd.crud.update_settings.assert_not_awaited()


async def test_cmd_newbie_seconds(base_data, patch_save):
    msg = make_message()
    await settings_cmd.cmd_newbie(msg, Cmd("3600"), **base_data)
    _, kwargs = settings_cmd.crud.update_settings.await_args
    assert kwargs["newbie_period"] == 3600
    assert kwargs["newbie_media_enabled"] is True


async def test_cmd_newbie_off(base_data, patch_save):
    msg = make_message()
    await settings_cmd.cmd_newbie(msg, Cmd("off"), **base_data)
    _, kwargs = settings_cmd.crud.update_settings.await_args
    assert kwargs == {"newbie_media_enabled": False}


async def test_cmd_newbie_rejects_out_of_range(base_data, patch_save):
    msg = make_message()
    await settings_cmd.cmd_newbie(msg, Cmd("5"), **base_data)  # below NEWBIE_PERIOD_MIN
    settings_cmd.crud.update_settings.assert_not_awaited()


async def test_non_admin_cannot_configure(base_data, patch_save):
    base_data["is_admin"] = False
    msg = make_message()
    await settings_cmd.cmd_antiflood(msg, Cmd("on"), **base_data)
    settings_cmd.crud.update_settings.assert_not_awaited()
