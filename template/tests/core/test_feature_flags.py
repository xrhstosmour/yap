"""Tests for the feature flags core service.

Tests the three-tier cache lookup, Redis sync, and fallback behavior.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from app.core.feature_flags import REDIS_CACHE_TTL
from app.core.feature_flags import _in_memory_cache
from app.core.feature_flags import feature_disabled
from app.core.feature_flags import feature_enabled
from app.core.feature_flags import refresh_cache
from app.core.feature_flags import remove_from_redis
from app.core.feature_flags import sync_to_redis


@pytest.fixture(autouse=True)
def clear_in_memory_cache() -> None:
    """Clear the in-memory cache before each test."""
    _in_memory_cache.clear()
    yield
    _in_memory_cache.clear()


@pytest.fixture(autouse=True)
def mock_redis() -> None:
    """Mock Redis client retrieval for all tests."""
    with patch("app.core.feature_flags._get_redis", return_value=None):
        yield


@pytest.mark.asyncio
async def test_feature_enabled_returns_false_for_unknown_flag() -> None:
    """Unknown flags should default to False."""
    result = await feature_enabled("nonexistent_feature")
    assert result is False


@pytest.mark.asyncio
async def test_feature_disabled_returns_true_for_unknown_flag() -> None:
    """feature_disabled should be the inverse of feature_enabled."""
    result = await feature_disabled("nonexistent_feature")
    assert result is True


@pytest.mark.asyncio
async def test_feature_enabled_uses_in_memory_cache() -> None:
    """Once fetched, a flag should be served from in-memory cache."""
    from app.core.feature_flags import _make_cache_entry

    _in_memory_cache["cached_flag"] = _make_cache_entry(True)

    result = await feature_enabled("cached_flag")
    assert result is True


@pytest.mark.asyncio
async def test_feature_enabled_falls_back_to_settings() -> None:
    """Settings defaults should be used when no DB entry exists."""
    with patch("app.core.feature_flags._get_settings_default", return_value=True):
        result = await feature_enabled("settings_enabled_flag")
        assert result is True


@pytest.mark.asyncio
async def test_refresh_cache_removes_from_memory() -> None:
    """refresh_cache should clear the in-memory entry."""
    from app.core.feature_flags import _make_cache_entry

    _in_memory_cache["temp_flag"] = _make_cache_entry(True)
    await refresh_cache("temp_flag")

    assert "temp_flag" not in _in_memory_cache


@pytest.mark.asyncio
async def test_sync_to_redis_updates_cache() -> None:
    """sync_to_redis should update in-memory cache."""
    mock_redis = MagicMock()
    mock_redis.set = AsyncMock()

    with patch("app.core.feature_flags._get_redis", return_value=mock_redis):
        await sync_to_redis("synced_flag", True)

    assert _in_memory_cache["synced_flag"]["state"] is True


@pytest.mark.asyncio
async def test_feature_disabled_matches_feature_enabled_inverse() -> None:
    """feature_disabled should always be the negation of feature_enabled."""
    from app.core.feature_flags import _make_cache_entry

    _in_memory_cache["test_inverse"] = _make_cache_entry(True)

    enabled = await feature_enabled("test_inverse")
    disabled = await feature_disabled("test_inverse")

    assert enabled is True
    assert disabled is False


@pytest.mark.asyncio
async def test_concurrent_feature_enabled_does_not_corrupt_cache() -> None:
    """Concurrent feature lookups should not corrupt in-memory cache."""
    flag_names = [f"concurrent_flag_{idx}" for idx in range(20)]

    results = await asyncio.gather(*(feature_enabled(name) for name in flag_names))

    assert len(results) == 20
    assert all(result is False for result in results)
    assert all(name in _in_memory_cache for name in flag_names)


# Redis interaction tests


@pytest.mark.asyncio
async def test_refresh_cache_clears_memory_and_redis() -> None:
    """refresh_cache should clear both in-memory cache and Redis key."""
    from app.core.feature_flags import _make_cache_entry

    _in_memory_cache["clear_flag"] = _make_cache_entry(True)

    mock_redis = MagicMock()
    mock_redis.delete = AsyncMock()

    with patch("app.core.feature_flags._get_redis", return_value=mock_redis):
        await refresh_cache("clear_flag")

    assert "clear_flag" not in _in_memory_cache
    mock_redis.delete.assert_awaited_once_with("feature_flags:clear_flag")


@pytest.mark.asyncio
async def test_sync_to_redis_sets_flag_in_redis() -> None:
    """sync_to_redis should call Redis SET and update memory cache."""
    _in_memory_cache.clear()

    mock_redis = MagicMock()
    mock_redis.set = AsyncMock()

    with patch("app.core.feature_flags._get_redis", return_value=mock_redis):
        await sync_to_redis("synced_redis_flag", True)

    mock_redis.set.assert_awaited_once_with(
        "feature_flags:synced_redis_flag", "true", ex=REDIS_CACHE_TTL
    )
    assert _in_memory_cache["synced_redis_flag"]["state"] is True


@pytest.mark.asyncio
async def test_redis_keys_written_with_an_expiry() -> None:
    """Every Redis write carries a TTL.

    An immortal key means a flag that drifted from the database, through a
    rolled-back write or a row deleted out of band, stays wrong until
    someone flushes Redis by hand.
    """
    _in_memory_cache.clear()

    mock_redis = MagicMock()
    mock_redis.set = AsyncMock()

    with patch("app.core.feature_flags._get_redis", return_value=mock_redis):
        await sync_to_redis("ttl_flag", False)

    _, kwargs = mock_redis.set.await_args
    assert kwargs.get("ex") == REDIS_CACHE_TTL
    assert REDIS_CACHE_TTL > 0


@pytest.mark.asyncio
async def test_remove_from_redis_deletes_from_redis() -> None:
    """remove_from_redis should call Redis DELETE and remove memory entry."""
    from app.core.feature_flags import _make_cache_entry

    _in_memory_cache["gone_flag"] = _make_cache_entry(True)

    mock_redis = MagicMock()
    mock_redis.delete = AsyncMock()

    with patch("app.core.feature_flags._get_redis", return_value=mock_redis):
        await remove_from_redis("gone_flag")

    assert "gone_flag" not in _in_memory_cache
    mock_redis.delete.assert_awaited_once_with("feature_flags:gone_flag")


# Settings default fallback


def test_get_settings_default_returns_false_for_unknown() -> None:
    """_get_settings_default should return False for flags not in settings."""
    from app.core.feature_flags import _get_settings_default

    result = _get_settings_default("completely_unknown_feature")
    assert result is False


# _is_cache_valid edge cases


def test_is_cache_valid_expired_returns_false() -> None:
    """_is_cache_valid should return False for an expired cache entry."""
    from app.core.feature_flags import _is_cache_valid

    old_entry = {"state": True, "cached_at": 0.0, "jitter": 1}
    assert _is_cache_valid(old_entry) is False


# _get_redis error handling


@pytest.mark.asyncio
async def test_get_redis_returns_none_on_import_failure() -> None:
    """_get_redis should return None when the Redis import/connection fails."""
    from app.core.feature_flags import _get_redis as _real_get_redis

    # Override the autouse fixture: restore the real _get_redis.
    # The real function imports get_redis from app.core.cache at call time;
    # we make that import raise to trigger the except path.
    with patch("app.core.feature_flags._get_redis", _real_get_redis):
        with patch("app.core.cache.get_redis", side_effect=RuntimeError("Redis down")):
            result = await _real_get_redis()
            assert result is None


# Redis miss (key not in Redis, falls to DB)


@pytest.mark.asyncio
async def test_feature_enabled_redis_miss_falls_through_to_db() -> None:
    """When Redis has no entry for the flag, fall through to DB/settings tier."""
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=None)

    with patch("app.core.feature_flags._get_redis", return_value=mock_redis):
        with patch("app.core.feature_flags._get_settings_default", return_value=True):
            result = await feature_enabled("redis_miss_flag")

    assert result is True
    mock_redis.get.assert_awaited_once_with("feature_flags:redis_miss_flag")


# sync_to_redis graceful degradation


@pytest.mark.asyncio
async def test_sync_to_redis_handles_redis_unavailable() -> None:
    """sync_to_redis should still update in-memory cache when Redis is down."""
    _in_memory_cache.clear()

    # The autouse fixture already makes _get_redis return None (simulating
    # Redis being unavailable). sync_to_redis skips the Redis SET and still
    # writes to the in-memory cache.
    await sync_to_redis("flag_no_redis", True)

    assert _in_memory_cache["flag_no_redis"]["state"] is True
