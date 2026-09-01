"""Feature flag core service with multi-tier caching.

Implements a three-tier lookup strategy (in-memory cache -> Redis -> Database)
with settings-based fallback defaults, modeled after production feature flag systems
used in large-scale web applications.

The in-memory cache uses TTL with random jitter to prevent thundering-herd
problems when flags expire simultaneously across multiple instances.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import TYPE_CHECKING
from typing import Any
from typing import TypedDict
from typing import cast

from app.core.logging import get_logger

try:
    from app.core.cache import CacheService
except ImportError:
    CacheService = None  # type: ignore

if TYPE_CHECKING:
    pass

logger = get_logger("feature_flags")

REDIS_PREFIX = "feature_flags"
FALLBACK_STATE = False
CACHE_TTL = 60

# Expiry on the shared Redis tier. Every write path already refreshes it
# explicitly, so this is the ceiling on how long a key that drifted from the
# database can stay wrong. Without it a stale key, one written by a rolled-back
# transaction, or one left behind by a flag deleted directly in the database,
# survived until someone flushed Redis by hand.
REDIS_CACHE_TTL = 300


class CacheEntry(TypedDict):
    state: bool
    cached_at: float
    jitter: int


_in_memory_cache: dict[str, CacheEntry] = {}
_cache_lock = asyncio.Lock()


def _jittered_ttl() -> int:
    """Return TTL with random jitter to prevent thundering herd.

    Returns:
        Seconds for cache TTL (base 60s + random up to 60s)
    """
    return CACHE_TTL + random.randint(0, 60)


def _make_cache_entry(state: bool) -> CacheEntry:
    """Create an in-memory cache entry with timestamp.

    Args:
        state: Current feature state

    Returns:
        Cache entry dict with state, timestamp, and jitter
    """
    return {
        "state": state,
        "cached_at": time.time(),
        "jitter": _jittered_ttl(),
    }


def _is_cache_valid(cached_data: CacheEntry | None) -> bool:
    """Check if cached data is still within TTL.

    Args:
        cached_data: The cached entry or None

    Returns:
        True if cache is present and not expired
    """
    if cached_data is None:
        return False

    elapsed = time.time() - cached_data["cached_at"]
    return elapsed < cached_data.get("jitter", CACHE_TTL)


async def _get_redis() -> Any:  # noqa: ANN401
    """Lazily import and get Redis connection.

    Returns:
        Redis client or None if unavailable
    """
    try:
        from app.core.cache import get_redis as _gr

        return await _gr()
    except Exception:
        return None


async def feature_enabled(name: str) -> bool:
    """Check if a feature flag is enabled.

    Performs a three-tier lookup:
    1. In-memory cache (fastest, TTL with jitter)
    2. Redis cache (cross-instance, persisted)
    3. Database (source of truth)
    4. Settings fallback (defaults) -> False

    Unknown features that are checked for the first time are
    auto-registered in the database with their default state.

    Args:
        name: The feature flag name

    Returns:
        True if the feature is enabled
    """
    # Tier 1: In-memory cache.
    async with _cache_lock:
        if name in _in_memory_cache:
            entry = _in_memory_cache[name]
            if _is_cache_valid(entry):
                return entry["state"]

    # Tier 2: Redis.
    redis_client = await _get_redis()
    if redis_client:
        redis_key = f"{REDIS_PREFIX}:{name}"
        state_at_redis = await redis_client.get(redis_key)
        if state_at_redis is not None:
            state = state_at_redis == "true" or state_at_redis is True
            async with _cache_lock:
                _in_memory_cache[name] = _make_cache_entry(state)
            return state

    # Tier 3: Database (source of truth).
    from app.database import async_session_factory
    from app.repositories.feature_flag_repository import FeatureFlagRepository

    try:
        async with async_session_factory() as session:
            repository = FeatureFlagRepository(session)
            flag = await repository.get_by_name(name)

            if flag is not None:
                state = flag.state
                async with _cache_lock:
                    _in_memory_cache[name] = _make_cache_entry(state)

                if redis_client:
                    await redis_client.set(
                        f"{REDIS_PREFIX}:{name}",
                        "true" if state else "false",
                        ex=REDIS_CACHE_TTL,
                    )

                return state
    except Exception:
        logger.warning("feature_flag_db_fetch_failed", name=name, exc_info=True)

    # Tier 4: Settings fallback -> False.
    fallback = _get_settings_default(name)
    async with _cache_lock:
        _in_memory_cache[name] = _make_cache_entry(fallback)
    return fallback


async def feature_disabled(name: str) -> bool:
    """Check if a feature flag is disabled.

    Args:
        name: The feature flag name

    Returns:
        True if the feature is disabled
    """
    return not await feature_enabled(name)


def _get_settings_default(name: str) -> bool:
    """Get fallback state from application settings.

    Checks the FEATURE_FLAGS dict in settings for a default value.
    Unknown flags default to False.

    Args:
        name: Feature flag name

    Returns:
        Default state
    """
    try:
        from app.core.settings import settings

        defaults = cast(
            dict[str, bool] | None, getattr(settings, "FEATURE_FLAGS", None)
        )
        if defaults is None:
            return FALLBACK_STATE
        return defaults.get(name, FALLBACK_STATE)
    except Exception:
        return FALLBACK_STATE


async def refresh_cache(name: str) -> None:
    """Force-refresh the cache for a feature flag.

    Removes the flag from in-memory cache and Redis so the next lookup
    will fetch from the database.

    Args:
        name: Feature flag name
    """
    async with _cache_lock:
        _in_memory_cache.pop(name, None)
    redis_client = await _get_redis()
    if redis_client:
        await redis_client.delete(f"{REDIS_PREFIX}:{name}")


async def sync_to_redis(name: str, state: bool) -> None:
    """Immediately sync a flag state to Redis.

    Only safe once the state is committed. Writing a state read from an
    open transaction publishes it to every instance before it is durable,
    and a rollback then leaves the whole deployment on a value the
    database never held. Call `refresh_cache` from inside a transaction
    instead, it invalidates rather than publishes.

    Args:
        name: Feature flag name
        state: New state to set
    """
    redis_client = await _get_redis()
    if redis_client:
        await redis_client.set(
            f"{REDIS_PREFIX}:{name}",
            "true" if state else "false",
            ex=REDIS_CACHE_TTL,
        )

    async with _cache_lock:
        _in_memory_cache[name] = _make_cache_entry(state)


async def remove_from_redis(name: str) -> None:
    """Remove a feature flag from Redis.

    Called when a flag is deleted from the database.

    Args:
        name: Feature flag name
    """
    redis_client = await _get_redis()
    if redis_client:
        await redis_client.delete(f"{REDIS_PREFIX}:{name}")

    async with _cache_lock:
        _in_memory_cache.pop(name, None)
