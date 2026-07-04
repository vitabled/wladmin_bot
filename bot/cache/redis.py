"""Redis client and cache utilities."""

import json
import logging
from typing import Any, Optional

import redis.asyncio as redis

logger = logging.getLogger(__name__)


class RedisClient:
    """Redis client wrapper for cache operations."""

    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self.client: Optional[redis.Redis] = None

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

    async def set(
        self, key: str, value: Any, ttl: Optional[int] = None
    ) -> bool:
        """Set value in cache."""
        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            await self.client.set(key, value, ex=ttl)
            return True
        except Exception as e:
            logger.error(f"Redis set error: {e}")
            return False

    async def get(self, key: str) -> Optional[str]:
        """Get value from cache."""
        try:
            return await self.client.get(key)
        except Exception as e:
            logger.error(f"Redis get error: {e}")
            return None

    async def get_json(self, key: str) -> Optional[Any]:
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

    async def incr(self, key: str, amount: int = 1) -> Optional[int]:
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
