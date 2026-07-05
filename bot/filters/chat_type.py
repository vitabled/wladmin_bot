"""Filters for chat type checks."""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import Message


class IsPrivate(BaseFilter):
    """Check if message is in a private chat."""

    async def __call__(self, message: Message) -> bool:
        return bool(message.chat and message.chat.type == "private")


class IsGroup(BaseFilter):
    """Check if message is in a group/supergroup."""

    async def __call__(self, message: Message) -> bool:
        return bool(message.chat and message.chat.type in ("group", "supergroup"))
