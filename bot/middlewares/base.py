"""Shared helpers for extracting chat/user from any Telegram event."""

from __future__ import annotations

from aiogram.types import CallbackQuery, ChatMemberUpdated, Message, TelegramObject
from aiogram.types import Chat as TgChat
from aiogram.types import User as TgUser

GROUP_TYPES = ("group", "supergroup")


def extract_chat(event: TelegramObject) -> TgChat | None:
    """Best-effort chat extraction across Message/CallbackQuery/ChatMember."""
    if isinstance(event, Message):
        return event.chat
    if isinstance(event, CallbackQuery):
        return event.message.chat if event.message else None
    if isinstance(event, ChatMemberUpdated):
        return event.chat
    return getattr(event, "chat", None)


def extract_user(event: TelegramObject) -> TgUser | None:
    """Best-effort acting-user extraction."""
    return getattr(event, "from_user", None)


def is_group(chat: TgChat | None) -> bool:
    return chat is not None and chat.type in GROUP_TYPES
