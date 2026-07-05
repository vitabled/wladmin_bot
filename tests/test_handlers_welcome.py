"""Tests for welcome message rendering/sending."""

from __future__ import annotations

from unittest.mock import AsyncMock

from bot.handlers import welcome
from tests.conftest import make_bot, make_chat, make_user


async def test_welcome_disabled_returns_none(translator, settings):
    settings["welcome_enabled"] = False
    result = await welcome.send_welcome(
        make_bot(), make_chat(), make_user(500, "V"), settings, translator
    )
    assert result is None


async def test_welcome_sends_html(monkeypatch, translator, settings):
    sent = AsyncMock(return_value="msg")
    monkeypatch.setattr(welcome, "safe_send_message", sent)
    bot = make_bot()
    bot.get_chat_member_count = AsyncMock(return_value=10)
    await welcome.send_welcome(
        bot, make_chat(title="Chat"), make_user(500, "V"), settings, translator
    )
    sent.assert_awaited_once()
    _, kwargs = sent.call_args
    assert kwargs.get("parse_mode") == "HTML"


async def test_welcome_escapes_user_name(monkeypatch, translator, settings):
    settings["welcome_text"] = "Hi {mention}"
    captured = {}

    async def fake_send(bot, chat_id, text, **kwargs):
        captured["text"] = text
        return "msg"

    monkeypatch.setattr(welcome, "safe_send_message", AsyncMock(side_effect=fake_send))
    bot = make_bot()
    bot.get_chat_member_count = AsyncMock(return_value=10)
    await welcome.send_welcome(
        bot, make_chat(), make_user(500, "<b>x</b>"), settings, translator
    )
    assert "&lt;b&gt;" in captured["text"]
    assert "<b>x</b>" not in captured["text"]


async def test_left_member_service_message_deleted(monkeypatch, base_data):
    from tests.conftest import make_message

    monkeypatch.setattr(welcome, "safe_delete_message", AsyncMock(return_value=True))
    msg = make_message()
    await welcome.on_member_left(msg, **base_data)
    welcome.safe_delete_message.assert_awaited_once()
