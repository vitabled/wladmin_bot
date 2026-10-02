"""Topic-tracker middleware: accumulate forum topics seen in group updates.

Telegram's Bot API cannot list a forum's topics (``channels.getForumTopics``
is user-account only), so the bot can only *learn* about topics from the
updates it receives. This outer ``dp.message`` middleware records every
group/supergroup/channel message that carries a ``message_thread_id`` and,
crucially, the ``forum_topic_created`` / ``forum_topic_edited`` service
messages that carry the topic NAME.

Each write uses its OWN short session from ``session_maker`` and is committed
immediately, so the record survives even if a downstream handler later fails
and the update session is rolled back. Every error is swallowed (logged at
debug): topic bookkeeping must never break the moderation pipeline.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.db import crud

logger = logging.getLogger(__name__)

_GROUP_TYPES = ("group", "supergroup", "channel")


class TopicTrackerMiddleware(BaseMiddleware):
    """Persist forum-topic sightings (title + activity) before handlers run."""

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self.session_maker = session_maker

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Duck-typed rather than ``isinstance(event, Message)``: the observer
        # may hand us any update that carries a ``chat`` (a plain Message for
        # this observer), while callbacks and inline queries never do.
        chat = getattr(event, "chat", None)
        if chat is not None and getattr(chat, "type", None) in _GROUP_TYPES:
            await self._track(event)
        return await handler(event, data)

    async def _track(self, message: Any) -> None:
        """Record the topic this message belongs to — best effort, never raises."""
        chat = message.chat
        if chat is None or chat.type not in _GROUP_TYPES:
            return

        thread_id = getattr(message, "message_thread_id", None)
        title: str | None = None
        created = getattr(message, "forum_topic_created", None)
        edited = getattr(message, "forum_topic_edited", None)
        is_rename = False
        if created is not None:
            title = getattr(created, "name", None)
            if thread_id is None:
                thread_id = message.message_id
        elif edited is not None:
            title = getattr(edited, "name", None)
            is_rename = True
            if thread_id is None:
                thread_id = message.message_id

        if thread_id is None:
            return

        try:
            async with self.session_maker() as session:
                if is_rename and title:
                    # A rename is not user activity: update the name only.
                    await crud.set_topic_title(session, chat.id, thread_id, title)
                else:
                    await crud.record_topic_seen(
                        session, chat.id, thread_id, title=title
                    )
                await session.commit()
        except Exception:  # noqa: BLE001 - bookkeeping is best-effort
            logger.debug("topic_tracker.record_failed", exc_info=True)
