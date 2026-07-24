"""Pytest configuration, shared fixtures and mocked-aiogram factories."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def sample_text():
    """Sample text for testing."""
    return "Hello, world! This is a test message."


@pytest.fixture
def sample_stopwords():
    """Sample stopwords list."""
    return ["spam", "bad", "evil", "hate"]


# --------------------------------------------------------------------------- #
# Mocked-aiogram factories for handler tests
# --------------------------------------------------------------------------- #

# Default chat settings (mirrors ChatSettings model defaults).
DEFAULT_SETTINGS: dict[str, Any] = {
    "welcome_enabled": True,
    "welcome_text": None,
    "delete_service_messages": True,
    "delete_welcome_after": None,
    "captcha_enabled": False,
    "captcha_type": "button",
    "captcha_timeout": 300,
    "captcha_fail_action": "kick",
    "warn_limit": 3,
    "warn_action": "mute",
    "warn_action_duration": None,
    "filter_links": False,
    "filter_forwards": False,
    "filter_stopwords": False,
    "antispam_action": "delete",
    "antispam_exempt_admins": True,
    "antiflood_enabled": False,
    "antiflood_limit": 5,
    "antiflood_window": 5,
    "antiflood_action": "mute",
    "newbie_media_enabled": False,
    "newbie_period": 3600,
    "triggers_enabled": False,
}

BOT_ID = 42
OWNER_ID = 999


def make_user(
    user_id: int,
    full_name: str = "User",
    username: str | None = None,
    is_bot: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        full_name=full_name,
        first_name=full_name,
        username=username,
        is_bot=is_bot,
        language_code="ru",
    )


def make_chat(
    chat_id: int = -1001234, chat_type: str = "supergroup", title: str = "Chat"
) -> SimpleNamespace:
    return SimpleNamespace(id=chat_id, type=chat_type, title=title)


def make_bot() -> MagicMock:
    bot = MagicMock()
    bot.id = BOT_ID
    for method in (
        "ban_chat_member",
        "unban_chat_member",
        "restrict_chat_member",
        "delete_message",
        "send_message",
        "get_chat_member",
        "get_chat",
        "get_chat_member_count",
    ):
        setattr(bot, method, AsyncMock())
    return bot


def make_message(
    *,
    text: str = "",
    chat: SimpleNamespace | None = None,
    from_user: SimpleNamespace | None = None,
    reply_to_message: Any = None,
    entities: list | None = None,
    sender_chat: Any = None,
    forward_origin: Any = None,
    is_automatic_forward: bool = False,
    new_chat_members: list | None = None,
    caption: str | None = None,
    content_type: str = "text",
    message_id: int = 555,
    bot: MagicMock | None = None,
) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.caption = caption
    msg.content_type = content_type
    msg.chat = chat or make_chat()
    msg.from_user = from_user or make_user(1000, "Actor", "actor")
    msg.reply_to_message = reply_to_message
    msg.entities = entities or []
    msg.sender_chat = sender_chat
    msg.forward_origin = forward_origin
    msg.is_automatic_forward = is_automatic_forward
    msg.new_chat_members = new_chat_members
    msg.message_id = message_id
    msg.bot = bot or make_bot()
    msg.reply = AsyncMock()
    msg.answer = AsyncMock()
    return msg


def make_callback(
    *,
    data: str,
    from_user: SimpleNamespace,
    chat: SimpleNamespace | None = None,
    bot: MagicMock | None = None,
) -> MagicMock:
    cb = MagicMock()
    cb.data = data
    cb.from_user = from_user
    cb.bot = bot or make_bot()
    message = MagicMock()
    message.chat = chat or make_chat()
    cb.message = message
    cb.answer = AsyncMock()
    return cb


class Cmd:
    """Minimal stand-in for aiogram's CommandObject."""

    def __init__(self, args: str | None = None) -> None:
        self.args = args


@pytest.fixture
def translator():
    """Translator that returns the key verbatim (assert on keys, not text)."""
    return lambda key, **kwargs: key


@pytest.fixture
def settings():
    return dict(DEFAULT_SETTINGS)


@pytest.fixture
def session():
    return AsyncMock()


@pytest.fixture
def redis():
    return AsyncMock()


@pytest.fixture
def base_data(translator, settings, session, redis):
    """The data dict middlewares would inject into a handler."""
    return {
        "_": translator,
        "lang": "ru",
        "settings": settings,
        "session": session,
        "redis": redis,
        "session_maker": MagicMock(),
        "is_admin": True,
        "is_owner": False,
        "owner_id": OWNER_ID,
        "event_chat": make_chat(),
        "event_user": make_user(1000, "Actor", "actor"),
    }
