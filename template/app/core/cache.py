"""Redis cache management using redis-py.

This module provides a centralized Redis client for caching,
rate limiting, and Celery broker connections.

The Redis client is initialized during application startup via
``init_redis()`` (called from the FastAPI lifespan). This avoids
lazy-initialization race conditions and supports both standalone
and cluster modes.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from collections.abc import Callable
from datetime import timedelta
from typing import Annotated
from typing import Any
from typing import cast

import redis.asyncio as redis
import redis.asyncio.cluster as redis_cluster
from fastapi import Depends
from redis.exceptions import ConnectionError
from redis.exceptions import TimeoutError

from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger("cache")


class _Missing:
    """Sentinel type for "this key is not in the cache"."""

    def __repr__(self) -> str:
        """Render as a readable marker in assertion output."""
        return "MISSING"


# Distinct from a cached `None`, which is a real value a `compute()` may
# legitimately return. See `CacheService._get_or_missing`.
MISSING = _Missing()

# Stampede-protection lock defaults for get_or_set().
LOCK_TTL_SECONDS = 10
WAIT_TIMEOUT_SECONDS = 5.0
RETRY_INTERVAL_SECONDS = 0.1

# Type alias covering both standalone and cluster Redis clients.
AsyncRedisClient = redis.Redis | redis_cluster.RedisCluster  # noqa: UP040

# Global Redis client and cache instances, initialized during lifespan.
_redis_client: AsyncRedisClient | None = None
_cache: CacheService | None = None


async def init_redis() -> None:
    """Initialize the shared Redis client during application startup.

    Creates a standalone or cluster client based on
    ``settings.REDIS_CLUSTER``. Must be called once from the
    FastAPI lifespan startup before any requests are served.

    Raises:
        RuntimeError: If already initialized.
    """
    global _redis_client

    if _redis_client is not None:
        raise RuntimeError("Redis client is already initialized")

    if settings.REDIS_CLUSTER:
        logger.info(
            "redis_cluster_initializing",
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
        )
        _redis_client = redis_cluster.RedisCluster(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD or None,
            encoding="utf-8",
            decode_responses=True,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
        )
    else:
        _redis_client = redis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
        )

    logger.info(
        "redis_connected",
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        cluster=settings.REDIS_CLUSTER,
    )


async def get_redis() -> AsyncRedisClient:
    """Return the shared Redis client.

    The client must have been initialized via ``init_redis()``
    during the application lifespan. This function is kept
    async for backward compatibility with existing call sites.

    Returns:
        Redis client instance (standalone or cluster)

    Raises:
        RuntimeError: If the client has not been initialized.
    """
    if _redis_client is None:
        raise RuntimeError(
            "Redis client not initialized. Ensure init_redis() "
            "is called during the application lifespan."
        )
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

    def __init__(self, redis_client: AsyncRedisClient, prefix: str = "cache") -> None:
        """Initialize cache service.

        Args:
            redis_client: Redis client instance (standalone or cluster)
            prefix: Key prefix for namespacing (default: "cache")
        """
        self.redis = redis_client
        self.prefix = prefix

    def _make_key(self, key: str) -> str:
        """Create namespaced cache key."""
        return f"{self.prefix}:{key}"

    async def _get_or_missing(self, key: str) -> Any:  # noqa: ANN401
        """Read a key, distinguishing "absent" from "cached as null".

        `get()` collapses both onto `None`, which is fine for its own
        contract but not for `get_or_set()`: a `compute()` that legitimately
        returns `None` would be stored, then read back as `None`, treated as
        a miss, and recomputed on every single call. See `get_or_set` for
        what that costs.

        Args:
            key: Cache key (without prefix)

        Returns:
            The decoded value, which may be `None`, or `MISSING` when the
            key is absent or Redis is unreachable.
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
            return MISSING

        if value is None:
            return MISSING

        try:
            return json.loads(value)
        except json.JSONDecodeError:
            logger.warning(
                "cache_deserialize_failed",
                key=full_key,
                value_preview=str(value)[:100],
            )
            return value

    async def get(self, key: str) -> Any:  # noqa: ANN401
        """Get value from cache.

        Args:
            key: Cache key (without prefix)

        Returns:
            Cached value or None if not found
        """
        value = await self._get_or_missing(key)
        return None if value is MISSING else value

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

    async def get_or_set(
        self,
        key: str,
        compute: Callable[[], Awaitable[Any]],
        ttl: int | timedelta | None = None,
        lock_ttl: int = LOCK_TTL_SECONDS,
        wait_timeout: float = WAIT_TIMEOUT_SECONDS,
        retry_interval: float = RETRY_INTERVAL_SECONDS,
    ) -> Any:  # noqa: ANN401
        """Get a cached value, computing it on miss with stampede protection.

        On a cache miss, callers race for a short-lived per-key lock
        (`SET NX EX`, mirroring `IdempotencyService.try_lock`). The
        winner computes the value, caches it, and releases the lock.
        Losers poll the cache for the winner's result; if it has not
        appeared before `wait_timeout` elapses (e.g. the winner died
        mid-compute), they fall through and compute their own value
        rather than waiting indefinitely.

        Args:
            key: Cache key (without prefix)
            compute: Async callable that produces the value on a miss
            ttl: Time-to-live for the cached value, or None for no expiration
            lock_ttl: Seconds the stampede lock is held before auto-expiring
            wait_timeout: Max seconds a lock-losing caller waits for the
                winner's result before computing independently
            retry_interval: Seconds between cache-read retries while waiting

        A `compute()` that returns `None` is cached and served like any
        other value. Testing the read against `None` instead of presence
        meant such a value was stored and then read back as a miss on every
        call, so it was recomputed every time and never served. The waiters
        below had it worse: they poll for a value that by construction never
        appears, so each one burned the full `wait_timeout` and then
        computed anyway. The stampede protection guaranteed a stampede, and
        added latency doing it.

        Returns:
            The cached or freshly computed value
        """
        cached = await self._get_or_missing(key)
        if cached is not MISSING:
            return cached

        full_key = self._make_key(key)
        lock_key = f"{full_key}:lock"

        acquired = False
        try:
            acquired = bool(await self.redis.set(lock_key, "1", nx=True, ex=lock_ttl))
        except (ConnectionError, TimeoutError) as error:
            logger.error(
                "cache_operation_failed",
                operation="get_or_set_lock",
                key=full_key,
                error=str(error),
            )
            # Redis is unavailable for locking; compute directly rather
            # than blocking, no stampede protection is possible here.
            return await compute()

        if acquired:
            try:
                value = await compute()
                await self.set(key, value, ttl=ttl)
                return value
            finally:
                try:
                    await self.redis.delete(lock_key)
                except (ConnectionError, TimeoutError) as error:
                    logger.error(
                        "cache_operation_failed",
                        operation="get_or_set_unlock",
                        key=full_key,
                        error=str(error),
                    )

        # Lost the race: wait briefly for the winner, then retry the read.
        deadline = time.monotonic() + wait_timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(retry_interval)
            cached = await self._get_or_missing(key)
            if cached is not MISSING:
                return cached

        # The winner did not publish a value in time; compute independently.
        return await compute()

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
        deleted = 0
        batch: list[str] = []

        try:
            async for key in self.redis.scan_iter(match=full_pattern):
                batch.append(key)
                if len(batch) >= 100:
                    deleted += cast(int, await self.redis.delete(*batch))
                    batch.clear()
            if batch:
                deleted += cast(int, await self.redis.delete(*batch))
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
            return deleted

        return deleted

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


# FastAPI dependency for injecting the Redis client into route handlers.
# Override in tests with app.dependency_overrides[get_redis] = mock.
RedisDependency = Annotated[AsyncRedisClient, Depends(get_redis)]
