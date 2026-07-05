"""Tests for reusable moderation actions (warn cascade, ban/mute/kick)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bot.db import crud
from bot.handlers import actions
from tests.conftest import make_bot


@pytest.fixture
def patched(monkeypatch):
    """Patch crud + safe wrappers used by actions; return the mocks."""
    mocks = {
        "add_warn": AsyncMock(return_value=1),
        "add_mod_log": AsyncMock(),
        "deactivate_all_warns": AsyncMock(return_value=0),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(crud, name, mock)
    for name in (
        "safe_ban_member",
        "safe_mute_member",
        "safe_kick_member",
        "safe_unban_member",
        "safe_unmute_member",
    ):
        monkeypatch.setattr(actions, name, AsyncMock(return_value=True))
    return mocks


async def test_do_warn_below_limit(patched, session, settings):
    patched["add_warn"].return_value = 1
    outcome = await actions.do_warn(make_bot(), session, -100, 1, 500, "spam", settings)
    assert outcome.count == 1
    assert outcome.limit == 3
    assert outcome.action_applied is None
    patched["deactivate_all_warns"].assert_not_awaited()


async def test_do_warn_reaches_limit_applies_action(patched, session, settings):
    patched["add_warn"].return_value = 3  # limit is 3
    outcome = await actions.do_warn(make_bot(), session, -100, 1, 500, None, settings)
    assert outcome.action_applied == "mute"  # default warn_action
    actions.safe_mute_member.assert_awaited()
    patched["deactivate_all_warns"].assert_awaited_once()


async def test_do_warn_ban_action(patched, session, settings):
    settings["warn_action"] = "ban"
    patched["add_warn"].return_value = 5
    outcome = await actions.do_warn(make_bot(), session, -100, 1, 500, None, settings)
    assert outcome.action_applied == "ban"
    actions.safe_ban_member.assert_awaited()


async def test_do_ban_logs_and_returns(patched, session):
    ok = await actions.do_ban(make_bot(), session, -100, 1, 500, None, "reason")
    assert ok is True
    actions.safe_ban_member.assert_awaited()
    crud.add_mod_log.assert_awaited()


async def test_do_mute_clamps_short_duration(patched, session, monkeypatch):
    captured = {}

    async def fake_mute(bot, chat_id, user_id, until_date=None):
        captured["until"] = until_date
        return True

    monkeypatch.setattr(actions, "safe_mute_member", AsyncMock(side_effect=fake_mute))
    # 5 seconds is below the 30s Telegram floor -> should be clamped, not None.
    ok = await actions.do_mute(make_bot(), session, -100, 1, 500, 5, None)
    assert ok is True
    assert captured["until"] is not None
