"""Handler tests for activity recording and /stats /top commands."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bot.db import crud
from bot.handlers import stats
from tests.conftest import Cmd, make_message, make_user


# --------------------------------------------------------------------------- #
# record_activity
# --------------------------------------------------------------------------- #
@pytest.fixture
def patch_bump(monkeypatch):
    monkeypatch.setattr(crud, "bump_activity", AsyncMock())


async def test_records_normal_message(base_data, patch_bump):
    msg = make_message(text="hi", from_user=make_user(7, "U"))
    await stats.record_activity(msg, base_data)
    crud.bump_activity.assert_awaited_once()


async def test_skips_when_disabled(base_data, patch_bump):
    base_data["settings"]["stats_enabled"] = False
    msg = make_message(text="hi", from_user=make_user(7, "U"))
    await stats.record_activity(msg, base_data)
    crud.bump_activity.assert_not_awaited()


async def test_skips_bot(base_data, patch_bump):
    msg = make_message(text="hi", from_user=make_user(7, "Bot", is_bot=True))
    await stats.record_activity(msg, base_data)
    crud.bump_activity.assert_not_awaited()


async def test_skips_anonymous(base_data, patch_bump):
    msg = make_message(text="hi", sender_chat=object())
    await stats.record_activity(msg, base_data)
    crud.bump_activity.assert_not_awaited()


# --------------------------------------------------------------------------- #
# /stats
# --------------------------------------------------------------------------- #
async def test_stats_display(base_data, monkeypatch):
    monkeypatch.setattr(crud, "get_activity", AsyncMock(return_value=12))
    monkeypatch.setattr(crud, "chat_activity_totals", AsyncMock(return_value=(100, 8)))
    msg = make_message(from_user=make_user(7, "U"))
    await stats.cmd_stats(msg, Cmd(None), **base_data)
    msg.reply.assert_awaited_once()


async def test_stats_toggle_off(base_data, monkeypatch):
    monkeypatch.setattr(crud, "update_settings", AsyncMock())
    msg = make_message(from_user=make_user(7, "U"))
    await stats.cmd_stats(msg, Cmd("off"), **base_data)
    _, kwargs = crud.update_settings.await_args
    assert kwargs == {"stats_enabled": False}


async def test_stats_toggle_requires_admin(base_data, monkeypatch):
    monkeypatch.setattr(crud, "update_settings", AsyncMock())
    base_data["is_admin"] = False
    msg = make_message(from_user=make_user(7, "U"))
    await stats.cmd_stats(msg, Cmd("on"), **base_data)
    crud.update_settings.assert_not_awaited()


# --------------------------------------------------------------------------- #
# /top
# --------------------------------------------------------------------------- #
async def test_top_empty(base_data, monkeypatch):
    monkeypatch.setattr(crud, "top_active", AsyncMock(return_value=[]))
    msg = make_message()
    await stats.cmd_top(msg, Cmd(None), **base_data)
    msg.reply.assert_awaited_once()


async def test_top_lists_users(base_data, monkeypatch):
    monkeypatch.setattr(crud, "top_active", AsyncMock(return_value=[(7, 50), (8, 30)]))
    monkeypatch.setattr(
        crud, "get_users_by_ids", AsyncMock(return_value={7: "Alice", 8: "Bob"})
    )
    msg = make_message()
    await stats.cmd_top(msg, Cmd("5"), **base_data)
    msg.reply.assert_awaited_once()
    # HTML mentions → parse_mode must be set.
    _, kwargs = msg.reply.await_args
    assert kwargs.get("parse_mode") == "HTML"
