"""Tests for the feature flags core service.

Tests the three-tier cache lookup, Redis sync, and fallback behavior.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from app.core.feature_flags import _in_memory_cache
from app.core.feature_flags import feature_disabled
from app.core.feature_flags import feature_enabled
from app.core.feature_flags import refresh_cache
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
