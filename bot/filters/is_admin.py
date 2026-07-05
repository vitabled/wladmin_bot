"""Filter: message sender is an admin/owner of the chat.

Полагается на ``is_admin``, вычисленный AdminMiddleware (owner / анонимный
админ / статус в чате). Если middleware не подключён (юнит-тесты) — считает сам.
"""

from __future__ import annotations

from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import Message

from bot.utils.telegram import get_chat_member_status


class IsAdmin(BaseFilter):
    """Pass only for chat admins / owner. Denies in private chats."""

    async def __call__(
        self, message: Message, is_admin: bool | None = None, **kwargs: Any
    ) -> bool:
        if is_admin is not None:
            return bool(is_admin)

        # Fallback (no AdminMiddleware): compute directly.
        if not message.chat or not message.from_user:
            return False
        if message.chat.type not in ("group", "supergroup"):
            return False
        if message.sender_chat and message.sender_chat.id == message.chat.id:
            return True
        status = await get_chat_member_status(
            message.bot, message.chat.id, message.from_user.id
        )
        return status in ("administrator", "creator")
