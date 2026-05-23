"""Redis cache management using redis-py.

This module provides a centralized Redis client for caching,
rate limiting, and Celery broker connections.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from typing import cast

import redis.asyncio as redis

from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger("cache")

# Global Redis client instance with async lock for thread-safe initialization.
_redis_client: redis.Redis | None = None
_redis_lock: asyncio.Lock = asyncio.Lock()


async def get_redis() -> redis.Redis:
    """Get or create Redis client connection.

    Returns a shared Redis client instance. Connection is
    established on first use and reused for subsequent calls.

    Uses an async lock to prevent concurrent clients from
    creating multiple connections during initialization.

    Returns:
        Redis client instance
    """
    global _redis_client

    if _redis_client is None:
        async with _redis_lock:
            if _redis_client is None:
                _redis_client = redis.from_url(
                    settings.REDIS_URL,
                    encoding="utf-8",
                    decode_responses=True,
                    max_connections=settings.REDIS_MAX_CONNECTIONS,
                )
                logger.info("redis_connected", url=settings.REDIS_HOST)

    return _redis_client


async def close_redis() -> None:
    """Close Redis connection gracefully."""
    global _cache
    global _redis_client

    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None
        logger.info("redis_closed")

    _cache = None


class CacheService:
    """Redis-based caching service.

    Provides simple key-value caching with automatic serialization.
    Supports TTL, prefix namespacing, and atomic operations.
    """

    def __init__(self, redis_client: redis.Redis, prefix: str = "cache") -> None:
        """Initialize cache service.

        Args:
            redis_client: Redis client instance
            prefix: Key prefix for namespacing (default: "cache")
        """
        self.redis = redis_client
        self.prefix = prefix

    def _make_key(self, key: str) -> str:
        """Create namespaced cache key."""
        return f"{self.prefix}:{key}"

    async def get(self, key: str) -> Any | None:
        """Get value from cache.

        Args:
            key: Cache key (without prefix)

        Returns:
            Cached value or None if not found
        """
        import json

        full_key = self._make_key(key)
        value = await self.redis.get(full_key)

        if value is not None:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value

        return None

    async def set(
        self,
        key: str,
        value: Any,
        ttl: int | timedelta | None = None,
    ) -> None:
        """Set value in cache with optional TTL.

        Args:
            key: Cache key (without prefix)
            value: Value to cache (will be JSON serialized)
            ttl: Time-to-live in seconds, or None for no expiration
        """
        import json

        full_key = self._make_key(key)
        serialized = json.dumps(value)

        if ttl is not None:
            if isinstance(ttl, timedelta):
                ttl = int(ttl.total_seconds())
            await self.redis.setex(full_key, ttl, serialized)
        else:
            await self.redis.set(full_key, serialized)

    async def delete(self, key: str) -> None:
        """Delete key from cache.

        Args:
            key: Cache key to delete
        """
        full_key = self._make_key(key)
        await self.redis.delete(full_key)

    async def delete_pattern(self, pattern: str) -> int:
        """Delete all keys matching pattern.

        Args:
            pattern: Pattern to match (e.g., "user:*")

        Returns:
            Number of keys deleted
        """
        full_pattern = self._make_key(pattern)
        keys = []

        async for key in self.redis.scan_iter(match=full_pattern):
            keys.append(key)

        if keys:
            return cast(int, await self.redis.delete(*keys))
        return 0

    async def exists(self, key: str) -> bool:
        """Check if key exists in cache.

        Args:
            key: Cache key to check

        Returns:
            True if key exists
        """
        full_key = self._make_key(key)
        return cast(int, await self.redis.exists(full_key)) > 0

    async def increment(self, key: str, amount: int = 1) -> int:
        """Atomically increment a counter.

        Args:
            key: Cache key
            amount: Amount to increment by

        Returns:
            New value after increment
        """
        full_key = self._make_key(key)
        return cast(int, await self.redis.incr(full_key, amount))

    async def get_ttl(self, key: str) -> int:
        """Get remaining TTL for a key.

        Args:
            key: Cache key

        Returns:
            TTL in seconds, -1 if no TTL, -2 if key doesn't exist
        """
        full_key = self._make_key(key)
        return cast(int, await self.redis.ttl(full_key))


# Global cache instance.
_cache: CacheService | None = None


async def get_cache() -> CacheService:
    """Get cache service instance."""
    global _cache

    if _cache is None:
        redis_client = await get_redis()
        _cache = CacheService(redis_client)

    return _cache
