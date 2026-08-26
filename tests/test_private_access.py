"""Tests for the private-chat access gate middleware (ЛС lockdown)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from bot.middlewares.private_access import PrivateAccessMiddleware
from tests.conftest import make_chat, make_message, make_user

# Real allowlist used in production (owner + work account).
ALLOWED = (1782827633, 7176227239)
INTRUDER = 123456789


def _make_handler() -> AsyncMock:
    """Handler spy that records calls and returns a sentinel value."""
    return AsyncMock(return_value="handled")


def _private_event(user_id: int | None = INTRUDER) -> MagicMock:
    """Message-like private-chat event; user_id=None → no from_user."""
    user = make_user(user_id) if user_id is not None else None
    msg = make_message(
        chat=make_chat(chat_id=111, chat_type="private"),
        from_user=user or make_user(0),
    )
    if user is None:
        msg.from_user = None  # make_message defaults None → drop it explicitly
    return msg


def _group_event(user_id: int = INTRUDER) -> MagicMock:
    return make_message(
        chat=make_chat(chat_id=-100111, chat_type="supergroup"),
        from_user=make_user(user_id),
    )


async def test_allowed_user_in_private_chat_passes():
    mw = PrivateAccessMiddleware(ALLOWED)
    handler = _make_handler()
    result = await mw(handler, _private_event(ALLOWED[0]), {})
    handler.assert_awaited_once()
    assert result == "handled"


async def test_second_allowed_user_in_private_chat_passes():
    mw = PrivateAccessMiddleware(ALLOWED)
    handler = _make_handler()
    result = await mw(handler, _private_event(ALLOWED[1]), {})
    handler.assert_awaited_once()
    assert result == "handled"


async def test_non_allowed_user_in_private_chat_is_dropped_and_logged(caplog):
    mw = PrivateAccessMiddleware(ALLOWED)
    handler = _make_handler()
    with caplog.at_level(logging.INFO, logger="bot.middlewares.private_access"):
        result = await mw(handler, _private_event(INTRUDER), {})
    assert result is None
    handler.assert_not_awaited()
    assert "dm.blocked" in caplog.text


async def test_missing_user_in_private_chat_is_dropped():
    mw = PrivateAccessMiddleware(ALLOWED)
    handler = _make_handler()
    result = await mw(handler, _private_event(user_id=None), {})
    assert result is None
    handler.assert_not_awaited()


async def test_group_chat_always_passes_even_for_non_allowed_user():
    mw = PrivateAccessMiddleware(ALLOWED)
    handler = _make_handler()
    result = await mw(handler, _group_event(INTRUDER), {})
    handler.assert_awaited_once()
    assert result == "handled"


async def test_empty_allowlist_passes_everything_in_private_chat():
    # Empty allowlist = no restriction (legacy behavior).
    mw = PrivateAccessMiddleware(())
    handler = _make_handler()
    result = await mw(handler, _private_event(INTRUDER), {})
    handler.assert_awaited_once()
    assert result == "handled"


async def test_uses_event_chat_and_event_user_from_data():
    # SettingsMiddleware stores event_chat/event_user in data; the gate must
    # honor them (AdminMiddleware reads them the same way).
    mw = PrivateAccessMiddleware(ALLOWED)
    handler = _make_handler()
    data = {
        "event_chat": make_chat(chat_id=222, chat_type="private"),
        "event_user": make_user(ALLOWED[0]),
    }
    result = await mw(handler, SimpleNamespace(), data)
    handler.assert_awaited_once()
    assert result == "handled"
