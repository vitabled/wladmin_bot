"""Middleware for loading chat settings."""

import logging
from typing import Any, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class SettingsMiddleware(BaseMiddleware):
    """Load chat settings from cache/database."""

    async def __call__(
        self,
        handler: Callable,
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        """Load settings into event data."""
        if event.chat:
            data["chat_id"] = event.chat.id
            data["chat_type"] = event.chat.type

        return await handler(event, data)


class DatabaseMiddleware(BaseMiddleware):
    """Add database session to event data."""

    def __init__(self, session_maker):
        self.session_maker = session_maker

    async def __call__(
        self,
        handler: Callable,
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        """Add session to data."""
        async with self.session_maker() as session:
            data["session"] = session
            return await handler(event, data)
