"""Tests for moderation target resolution."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.db import crud
from bot.utils.targets import resolve_target
from tests.conftest import make_bot, make_message, make_user


async def test_resolve_from_reply(session):
    victim = make_user(500, "Victim", "victim")
    reply = SimpleNamespace(sender_chat=None, from_user=victim)
    msg = make_message(reply_to_message=reply)
    target, err, consumed = await resolve_target(msg, [], session, make_bot())
    assert err is None
    assert target.user_id == 500
    assert consumed == 0  # reply consumes no positional args


async def test_resolve_reply_to_channel_refused(session):
    reply = SimpleNamespace(sender_chat=SimpleNamespace(id=-100999), from_user=None)
    msg = make_message(reply_to_message=reply)
    target, err, consumed = await resolve_target(msg, [], session, make_bot())
    assert target is None
    assert err == "error_cannot_act_on_channel"


async def test_resolve_numeric_id(session):
    msg = make_message()
    target, err, consumed = await resolve_target(
        msg, ["12345", "30m"], session, make_bot()
    )
    assert err is None
    assert target.user_id == 12345
    assert consumed == 1


async def test_resolve_no_target(session):
    msg = make_message()
    target, err, _ = await resolve_target(msg, [], session, make_bot())
    assert target is None
    assert err == "error_no_target"


async def test_resolve_username_from_db(session, monkeypatch):
    cached = SimpleNamespace(user_id=777, first_name="Cached", username="cached")
    monkeypatch.setattr(crud, "get_user_by_username", AsyncMock(return_value=cached))
    msg = make_message()
    target, err, consumed = await resolve_target(msg, ["@cached"], session, make_bot())
    assert err is None
    assert target.user_id == 777
    assert consumed == 1


async def test_resolve_username_via_api(session, monkeypatch):
    monkeypatch.setattr(crud, "get_user_by_username", AsyncMock(return_value=None))
    bot = make_bot()
    bot.get_chat = AsyncMock(
        return_value=SimpleNamespace(
            type="private", id=888, full_name="Api User", username="apiuser"
        )
    )
    msg = make_message()
    target, err, _ = await resolve_target(msg, ["@apiuser"], session, bot)
    assert err is None
    assert target.user_id == 888


async def test_resolve_username_not_found(session, monkeypatch):
    monkeypatch.setattr(crud, "get_user_by_username", AsyncMock(return_value=None))
    bot = make_bot()
    bot.get_chat = AsyncMock(side_effect=Exception("no such user"))
    msg = make_message()
    target, err, _ = await resolve_target(msg, ["@ghost"], session, bot)
    assert target is None
    assert err == "error_target_not_found"


async def test_resolve_multidash_token_no_crash(session):
    """Regression: '--5' must not crash int(); it resolves to no target."""
    msg = make_message()
    target, err, _ = await resolve_target(msg, ["--5"], session, make_bot())
    assert target is None
    assert err == "error_no_target"


async def test_resolve_text_mention(session):
    victim = make_user(600, "Mentioned", None)
    entity = SimpleNamespace(type="text_mention", user=victim)
    msg = make_message(entities=[entity])
    target, err, consumed = await resolve_target(
        msg, ["Mentioned"], session, make_bot()
    )
    assert err is None
    assert target.user_id == 600
    assert consumed == 1
