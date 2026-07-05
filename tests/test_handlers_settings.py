"""Handler tests for settings commands."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bot.db import crud
from bot.handlers import settings_cmd
from tests.conftest import Cmd, make_message


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch):
    monkeypatch.setattr(crud, "update_settings", AsyncMock())
    monkeypatch.setattr(crud, "add_stopword", AsyncMock(return_value=True))
    monkeypatch.setattr(crud, "remove_stopword", AsyncMock(return_value=True))
    monkeypatch.setattr(crud, "list_stopwords", AsyncMock(return_value=[]))


async def test_settings_denied_non_admin(base_data):
    base_data["is_admin"] = False
    msg = make_message()
    await settings_cmd.cmd_settings(msg, Cmd(), **base_data)
    msg.reply.assert_awaited_once_with("error_not_admin")


async def test_setwarnlimit_invalid(base_data):
    msg = make_message()
    await settings_cmd.cmd_setwarnlimit(msg, Cmd(args="abc"), **base_data)
    msg.reply.assert_awaited_once_with("warn_limit_invalid")


async def test_setwarnlimit_out_of_range(base_data):
    msg = make_message()
    await settings_cmd.cmd_setwarnlimit(msg, Cmd(args="0"), **base_data)
    msg.reply.assert_awaited_once_with("warn_limit_invalid")


async def test_setwarnlimit_valid(base_data):
    msg = make_message()
    await settings_cmd.cmd_setwarnlimit(msg, Cmd(args="5"), **base_data)
    crud.update_settings.assert_awaited_once()
    msg.reply.assert_awaited_once_with("warn_limit_set")


async def test_welcome_toggle_invalid(base_data):
    msg = make_message()
    await settings_cmd.cmd_welcome(msg, Cmd(args="maybe"), **base_data)
    msg.reply.assert_awaited_once_with("usage_on_off")


async def test_welcome_toggle_on(base_data):
    msg = make_message()
    await settings_cmd.cmd_welcome(msg, Cmd(args="on"), **base_data)
    crud.update_settings.assert_awaited_once()
    msg.reply.assert_awaited_once_with("ok_enabled")


async def test_setcaptcha_invalid(base_data):
    msg = make_message()
    await settings_cmd.cmd_setcaptcha(msg, Cmd(args="rot13"), **base_data)
    msg.reply.assert_awaited_once_with("captcha_type_invalid")


async def test_setcaptcha_valid(base_data):
    msg = make_message()
    await settings_cmd.cmd_setcaptcha(msg, Cmd(args="math"), **base_data)
    crud.update_settings.assert_awaited_once()
    msg.reply.assert_awaited_once_with("captcha_type_set")


async def test_addstop_empty(base_data):
    msg = make_message()
    await settings_cmd.cmd_addstop(msg, Cmd(args="  "), **base_data)
    msg.reply.assert_awaited_once_with("stopword_empty")


async def test_addstop_word(base_data):
    msg = make_message()
    await settings_cmd.cmd_addstop(msg, Cmd(args="Spam"), **base_data)
    crud.add_stopword.assert_awaited_once()
    base_data["redis"].invalidate_stopwords.assert_awaited_once()
    msg.reply.assert_awaited_once_with("stopword_added")


async def test_antispam_usage(base_data):
    msg = make_message()
    await settings_cmd.cmd_antispam(msg, Cmd(args="links"), **base_data)
    msg.reply.assert_awaited_once_with("antispam_usage")


async def test_antispam_set(base_data):
    msg = make_message()
    await settings_cmd.cmd_antispam(msg, Cmd(args="links on"), **base_data)
    crud.update_settings.assert_awaited_once()
    msg.reply.assert_awaited_once_with("antispam_set")


async def test_setwarnaction_invalid(base_data):
    msg = make_message()
    await settings_cmd.cmd_setwarnaction(msg, Cmd(args="explode"), **base_data)
    msg.reply.assert_awaited_once_with("warn_action_invalid")


async def test_setwarnaction_valid_with_duration(base_data):
    msg = make_message()
    await settings_cmd.cmd_setwarnaction(msg, Cmd(args="mute 2h"), **base_data)
    crud.update_settings.assert_awaited_once()
    msg.reply.assert_awaited_once_with("warn_action_set")
