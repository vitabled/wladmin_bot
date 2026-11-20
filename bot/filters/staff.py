"""Filters for bot-wide staff roles."""

from __future__ import annotations

from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from bot.middlewares.base import extract_user
from bot.services.staff import ROLE_MODERATOR, get_role, role_at_least


class IsStaff(BaseFilter):
    """Allow users whose bot staff role meets the requested minimum."""

    def __init__(self, min_role: str = ROLE_MODERATOR) -> None:
        self.min_role = min_role

    async def __call__(self, event: TelegramObject, **data: Any) -> bool:
        if "staff_role" in data:
            role = data["staff_role"]
        else:
            user = data.get("event_user") or extract_user(event)
            session = data.get("session")
            if user is None or session is None:
                return False
            role = await get_role(session, user.id)

        return bool(role is not None and role_at_least(role, self.min_role))
