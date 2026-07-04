"""Filters for chat type checks."""

from aiogram.filters import BaseFilter
from aiogram.types import Message


class IsPrivate(BaseFilter):
    """Check if message is in private chat."""

    async def __call__(self, message: Message) -> bool:
        return message.chat and message.chat.type == "private"


class IsGroup(BaseFilter):
    """Check if message is in group."""

    async def __call__(self, message: Message) -> bool:
        return message.chat and message.chat.type in ["group", "supergroup"]
