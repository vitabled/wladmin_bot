"""Redis client and cache utilities."""

import json
import logging
from typing import Any

import redis.asyncio as redis

logger = logging.getLogger(__name__)


class RedisClient:
    """Redis client wrapper for cache operations."""

    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self.client: redis.Redis | None = None

    async def connect(self) -> None:
        """Connect to Redis."""
        self.client = await redis.from_url(self.redis_url, decode_responses=True)
        await self.client.ping()
        logger.info("Connected to Redis")

    async def disconnect(self) -> None:
        """Disconnect from Redis."""
        if self.client:
            await self.client.close()
            logger.info("Disconnected from Redis")

    async def set(self, key: str, value: Any, ttl: int | None = None) -> bool:
        """Set value in cache."""
        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            await self.client.set(key, value, ex=ttl)
            return True
        except Exception as e:
            logger.error(f"Redis set error: {e}")
            return False

    async def get(self, key: str) -> str | None:
        """Get value from cache."""
        try:
            return await self.client.get(key)
        except Exception as e:
            logger.error(f"Redis get error: {e}")
            return None

    async def get_json(self, key: str) -> Any | None:
        """Get JSON value from cache."""
        value = await self.get(key)
        if value:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return None
        return None

    async def delete(self, key: str) -> bool:
        """Delete key from cache."""
        try:
            await self.client.delete(key)
            return True
        except Exception as e:
            logger.error(f"Redis delete error: {e}")
            return False

    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        try:
            return await self.client.exists(key) > 0
        except Exception as e:
            logger.error(f"Redis exists error: {e}")
            return False

    async def incr(self, key: str, amount: int = 1) -> int | None:
        """Increment counter."""
        try:
            return await self.client.incrby(key, amount)
        except Exception as e:
            logger.error(f"Redis incr error: {e}")
            return None

    async def expire(self, key: str, ttl: int) -> bool:
        """Set expiration for key."""
        try:
            await self.client.expire(key, ttl)
            return True
        except Exception as e:
            logger.error(f"Redis expire error: {e}")
            return False

    async def flush(self) -> bool:
        """Flush all data (for testing)."""
        try:
            await self.client.flushdb()
            return True
        except Exception as e:
            logger.error(f"Redis flush error: {e}")
            return False

    # --- Domain helpers: chat settings cache ------------------------------
    @staticmethod
    def _settings_key(chat_id: int) -> str:
        return f"chat_settings:{chat_id}"

    async def cache_settings(self, chat_id: int, data: dict, ttl: int = 3600) -> None:
        """Cache a chat's settings dict with TTL."""
        await self.set(self._settings_key(chat_id), data, ttl=ttl)

    async def get_cached_settings(self, chat_id: int) -> dict | None:
        """Return cached settings dict or None on miss."""
        value = await self.get_json(self._settings_key(chat_id))
        return value if isinstance(value, dict) else None

    async def invalidate_settings(self, chat_id: int) -> None:
        """Drop cached settings after a change."""
        await self.delete(self._settings_key(chat_id))

    # --- Domain helpers: stopwords cache ----------------------------------
    @staticmethod
    def _stopwords_key(chat_id: int) -> str:
        return f"stopwords:{chat_id}"

    async def cache_stopwords(self, chat_id: int, words: list, ttl: int = 3600) -> None:
        """Cache a chat's stopword list with TTL."""
        await self.set(self._stopwords_key(chat_id), words, ttl=ttl)

    async def get_cached_stopwords(self, chat_id: int) -> list | None:
        """Return cached stopword list or None on miss."""
        value = await self.get_json(self._stopwords_key(chat_id))
        return value if isinstance(value, list) else None

    async def invalidate_stopwords(self, chat_id: int) -> None:
        """Drop cached stopwords after a change."""
        await self.delete(self._stopwords_key(chat_id))

    # --- Domain helpers: pending captcha ----------------------------------
    @staticmethod
    def _captcha_key(chat_id: int, user_id: int) -> str:
        return f"captcha:{chat_id}:{user_id}"

    async def set_captcha(
        self, chat_id: int, user_id: int, data: dict, ttl: int
    ) -> None:
        """Store pending-captcha state; auto-expires after ``ttl`` seconds."""
        await self.set(self._captcha_key(chat_id, user_id), data, ttl=ttl)

    async def get_captcha(self, chat_id: int, user_id: int) -> dict | None:
        """Return pending-captcha state or None if solved/expired."""
        value = await self.get_json(self._captcha_key(chat_id, user_id))
        return value if isinstance(value, dict) else None

    async def delete_captcha(self, chat_id: int, user_id: int) -> bool:
        """Clear pending-captcha state (on solve). True if a key was removed."""
        try:
            removed = await self.client.delete(self._captcha_key(chat_id, user_id))
            return bool(removed)
        except Exception as e:
            logger.error(f"Redis delete_captcha error: {e}")
            return False
