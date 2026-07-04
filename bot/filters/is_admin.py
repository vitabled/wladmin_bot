"""Filter to check if user is admin in chat."""

from aiogram.filters import BaseFilter
from aiogram.types import Message


class IsAdmin(BaseFilter):
    """Check if message sender is admin in chat."""

    async def __call__(self, message: Message) -> bool:
        """Check if sender is admin."""
        if not message.chat or not message.from_user:
            return False

        if message.chat.type not in ["group", "supergroup"]:
            return True

        try:
            member = await message.bot.get_chat_member(
                message.chat.id, message.from_user.id
            )
            return member.is_admin
        except Exception:
            return False
