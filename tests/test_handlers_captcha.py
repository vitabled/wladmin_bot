"""Handler tests for the captcha flow."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bot.db import crud
from bot.handlers import captcha
from tests.conftest import make_callback, make_message, make_user


@pytest.fixture(autouse=True)
def patch_captcha(monkeypatch):
    monkeypatch.setattr(captcha, "safe_unmute_member", AsyncMock(return_value=True))
    monkeypatch.setattr(captcha, "safe_delete_message", AsyncMock(return_value=True))
    monkeypatch.setattr(captcha, "safe_mute_member", AsyncMock(return_value=True))
    monkeypatch.setattr(captcha, "send_welcome", AsyncMock())
    monkeypatch.setattr(captcha, "_start_captcha", AsyncMock())
    monkeypatch.setattr(crud, "upsert_user", AsyncMock())
    # Phase 8: no federation by default, so the on-join fed-ban check is skipped.
    monkeypatch.setattr(crud, "get_chat_federation", AsyncMock(return_value=None))


async def test_wrong_user_cannot_solve(base_data):
    # callback targets user 500, but stranger 999 presses it.
    cb = make_callback(data="captcha:-100:500:ok", from_user=make_user(999, "Stranger"))
    await captcha.on_captcha_answer(cb, **base_data)
    cb.answer.assert_awaited_once_with("captcha_not_for_you", show_alert=True)


async def test_button_correct_solves(base_data):
    base_data["redis"].get_captcha = AsyncMock(
        return_value={"type": "button", "answer": "", "message_id": 10}
    )
    base_data["redis"].delete_captcha = AsyncMock()
    cb = make_callback(data="captcha:-100:500:ok", from_user=make_user(500, "Newbie"))
    await captcha.on_captcha_answer(cb, **base_data)
    captcha.safe_unmute_member.assert_awaited_once()
    captcha.send_welcome.assert_awaited_once()


async def test_no_pending_double_press(base_data):
    base_data["redis"].get_captcha = AsyncMock(return_value=None)
    cb = make_callback(data="captcha:-100:500:ok", from_user=make_user(500, "Newbie"))
    await captcha.on_captcha_answer(cb, **base_data)
    cb.answer.assert_awaited_once_with()  # silent ack, no alert
    captcha.safe_unmute_member.assert_not_awaited()


async def test_concurrent_press_loses_atomic_claim(base_data):
    # Both presses read pending, but Redis DEL removed 0 keys for this caller
    # (the other concurrent task already claimed it) -> no double unmute/welcome.
    base_data["redis"].get_captcha = AsyncMock(
        return_value={"type": "button", "answer": "", "message_id": 10}
    )
    base_data["redis"].delete_captcha = AsyncMock(return_value=False)
    cb = make_callback(data="captcha:-100:500:ok", from_user=make_user(500, "Newbie"))
    await captcha.on_captcha_answer(cb, **base_data)
    captcha.safe_unmute_member.assert_not_awaited()
    captcha.send_welcome.assert_not_awaited()


async def test_math_wrong_answer(base_data):
    base_data["redis"].get_captcha = AsyncMock(
        return_value={"type": "math", "answer": "8", "message_id": 10}
    )
    cb = make_callback(data="captcha:-100:500:9", from_user=make_user(500, "Newbie"))
    await captcha.on_captcha_answer(cb, **base_data)
    cb.answer.assert_awaited_once_with("captcha_wrong", show_alert=True)
    captcha.safe_unmute_member.assert_not_awaited()


async def test_new_member_captcha_enabled(base_data):
    base_data["settings"]["captcha_enabled"] = True
    newbie = make_user(500, "Newbie", "newbie")
    msg = make_message(new_chat_members=[newbie])
    await captcha.on_new_members(msg, **base_data)
    captcha._start_captcha.assert_awaited_once()
    captcha.send_welcome.assert_not_awaited()


async def test_new_member_welcome_when_captcha_disabled(base_data):
    base_data["settings"]["captcha_enabled"] = False
    newbie = make_user(500, "Newbie", "newbie")
    msg = make_message(new_chat_members=[newbie])
    await captcha.on_new_members(msg, **base_data)
    captcha.send_welcome.assert_awaited_once()
    captcha._start_captcha.assert_not_awaited()


async def test_fedbanned_member_banned_on_join(base_data, monkeypatch):
    from types import SimpleNamespace

    base_data["settings"]["captcha_enabled"] = True
    monkeypatch.setattr(
        crud, "get_chat_federation", AsyncMock(return_value=SimpleNamespace(id=5))
    )
    monkeypatch.setattr(crud, "is_fedbanned", AsyncMock(return_value=True))
    monkeypatch.setattr(captcha, "safe_ban_member", AsyncMock(return_value=True))
    newbie = make_user(500, "Banned", "banned")
    msg = make_message(new_chat_members=[newbie])
    await captcha.on_new_members(msg, **base_data)
    captcha.safe_ban_member.assert_awaited_once()
    captcha._start_captcha.assert_not_awaited()
    captcha.send_welcome.assert_not_awaited()


async def test_new_member_bot_ignored(base_data):
    base_data["settings"]["captcha_enabled"] = True
    bot_user = make_user(700, "SomeBot", "somebot", is_bot=True)
    msg = make_message(new_chat_members=[bot_user])
    await captcha.on_new_members(msg, **base_data)
    captcha._start_captcha.assert_not_awaited()
    captcha.send_welcome.assert_not_awaited()
