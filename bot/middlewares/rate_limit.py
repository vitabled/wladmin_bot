"""Rate limiting middleware for DoS protection."""

import logging
from typing import Any, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message

from bot.cache.redis import RedisClient

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseMiddleware):
    """Implement per-user/per-chat rate limiting using Redis."""

    def __init__(self, redis: RedisClient, limit: int = 10, window: int = 60):
        """
        Initialize rate limiting.

        Args:
            redis: Redis client
            limit: Number of requests allowed
            window: Time window in seconds
        """
        self.redis = redis
        self.limit = limit
        self.window = window

    async def __call__(
        self,
        handler: Callable,
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        """Check rate limits before processing."""
        if not event.from_user or not event.chat:
            return await handler(event, data)

        key = f"ratelimit:{event.chat.id}:{event.from_user.id}"

        try:
            current = await self.redis.incr(key)

            if current == 1:
                await self.redis.expire(key, self.window)

            if current > self.limit:
                logger.warning(
                    f"Rate limit exceeded: user={event.from_user.id}, "
                    f"chat={event.chat.id}, count={current}"
                )
                await event.answer(
                    "⏱️ Too many requests. Please slow down.",
                )
                return

        except Exception as e:
            logger.error(f"Rate limit check failed: {e}")

        return await handler(event, data)
