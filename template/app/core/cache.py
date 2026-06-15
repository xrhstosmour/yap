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
from redis.exceptions import ConnectionError
from redis.exceptions import TimeoutError

from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger("cache")

# Global Redis client instance with async lock for thread-safe initialization.
_redis_client: redis.Redis | None = None
_redis_lock: asyncio.Lock = asyncio.Lock()

# Global cache instance.
_cache: CacheService | None = None


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

    async def get(self, key: str) -> Any:  # noqa: ANN401
        """Get value from cache.

        Args:
            key: Cache key (without prefix)

        Returns:
            Cached value or None if not found
        """
        import json

        full_key = self._make_key(key)
        try:
            value = await self.redis.get(full_key)
        except (
            ConnectionError,
            TimeoutError,
        ) as error:
            logger.error(
                "cache_operation_failed",
                operation="get",
                key=full_key,
                error=str(error),
            )
            return None

        if value is not None:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                logger.warning(
                    "cache_deserialize_failed",
                    key=full_key,
                    value_preview=str(value)[:100],
                )
                return value

        return None

    async def set(
        self,
        key: str,
        value: object,
        ttl: int | timedelta | None = None,
    ) -> bool:
        """Set value in cache with optional TTL.

        Args:
            key: Cache key (without prefix)
            value: Value to cache (will be JSON serialized)
            ttl: Time-to-live in seconds, or None for no expiration

        Returns:
            True if value was cached successfully, False otherwise
        """
        import json

        full_key = self._make_key(key)
        serialized = json.dumps(value)

        max_size = getattr(settings, "CACHE_MAX_VALUE_SIZE", 1_048_576)
        if len(serialized) > max_size:
            logger.warning(
                "cache_value_too_large",
                key=full_key,
                size=len(serialized),
                max_size=max_size,
            )
            return False

        try:
            if ttl is not None:
                if isinstance(ttl, timedelta):
                    ttl = int(ttl.total_seconds())
                await self.redis.setex(full_key, ttl, serialized)
            else:
                await self.redis.set(full_key, serialized)
            return True
        except (
            ConnectionError,
            TimeoutError,
        ) as error:
            logger.error(
                "cache_operation_failed",
                operation="set",
                key=full_key,
                error=str(error),
            )
            return False

    async def delete(self, key: str) -> bool:
        """Delete key from cache.

        Args:
            key: Cache key to delete

        Returns:
            True if deletion succeeded, False otherwise
        """
        full_key = self._make_key(key)
        try:
            await self.redis.delete(full_key)
            return True
        except (
            ConnectionError,
            TimeoutError,
        ) as error:
            logger.error(
                "cache_operation_failed",
                operation="delete",
                key=full_key,
                error=str(error),
            )
            return False

    async def delete_pattern(self, pattern: str) -> int:
        """Delete all keys matching pattern.

        Args:
            pattern: Pattern to match (e.g., "user:*")

        Returns:
            Number of keys deleted
        """
        full_pattern = self._make_key(pattern)
        keys_set: set[str] = set()

        try:
            async for key in self.redis.scan_iter(match=full_pattern):
                keys_set.add(key)
        except (
            ConnectionError,
            TimeoutError,
        ) as error:
            logger.error(
                "cache_operation_failed",
                operation="scan",
                key=full_pattern,
                error=str(error),
            )
            return 0

        if keys_set:
            try:
                return cast(int, await self.redis.delete(*keys_set))
            except (
                ConnectionError,
                TimeoutError,
            ) as error:
                logger.error(
                    "cache_operation_failed",
                    operation="delete_pattern",
                    key=full_pattern,
                    error=str(error),
                )
                return 0
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


async def get_cache() -> CacheService:
    """Get cache service instance."""
    global _cache

    if _cache is None:
        redis_client = await get_redis()
        _cache = CacheService(redis_client)

    return _cache
