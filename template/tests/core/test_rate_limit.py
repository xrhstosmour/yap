"""Tests for rate limiting functionality.

Tests cover:
- RateLimiter._make_key() prefix logic
- RateLimitExceeded exception (HTTP 429)
- RateLimiter.check_rate_limit() with mocked Redis (allowed / denied paths)
- check_user_rate_limit() and check_api_key_rate_limit() wrappers
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest

# Import module BEFORE the autouse disable_rate_limit fixture in conftest
# replaces check_user_rate_limit / check_api_key_rate_limit with no-ops.
# Capture references to the original functions for direct testing.
import app.core.rate_limit as _rl_mod
from app.core.rate_limit import RateLimiter
from app.core.rate_limit import RateLimitExceeded

_original_check_user = _rl_mod.check_user_rate_limit
_original_check_api_key = _rl_mod.check_api_key_rate_limit


# _make_key()


def test_make_key_default_prefix() -> None:
    """Default key prefix should be 'ratelimit'."""
    limiter = RateLimiter(requests_per_minute=10)
    assert limiter._make_key("user-42") == "ratelimit:user-42"


def test_make_key_custom_prefix() -> None:
    """Custom key prefix should be reflected in the key."""
    limiter = RateLimiter(requests_per_minute=10, key_prefix="custom:ns")
    assert limiter._make_key("abc") == "custom:ns:abc"


def test_make_key_empty_identifier() -> None:
    """Key with empty identifier should still produce a namespaced key."""
    limiter = RateLimiter(requests_per_minute=10, key_prefix="pfx")
    assert limiter._make_key("") == "pfx:"


# RateLimitExceeded


class TestRateLimitExceeded:
    """Verify the RateLimitExceeded exception carries HTTP 429 semantics."""

    def test_is_http_exception(self) -> None:
        """RateLimitExceeded must be an HTTPException subclass."""
        from fastapi import HTTPException

        exc = RateLimitExceeded(retry_after=5)
        assert isinstance(exc, HTTPException)

    def test_status_code_is_429(self) -> None:
        """Status code must be 429 Too Many Requests."""
        exc = RateLimitExceeded(retry_after=30)
        assert exc.status_code == 429

    def test_detail_message(self) -> None:
        """Detail should be 'Rate limit exceeded'."""
        exc = RateLimitExceeded(retry_after=30)
        assert exc.detail == "Rate limit exceeded"

    def test_retry_after_header_is_string(self) -> None:
        """Retry-After header must be the string representation of seconds."""
        exc = RateLimitExceeded(retry_after=42)
        assert exc.headers == {"Retry-After": "42"}

    def test_retry_after_zero(self) -> None:
        """Even zero retry_after should produce a valid header."""
        exc = RateLimitExceeded(retry_after=0)
        assert exc.headers == {"Retry-After": "0"}


# RateLimiter.check_rate_limit()


@pytest.fixture
def mock_redis_client() -> AsyncMock:
    """Return an AsyncMock that mimics a redis.asyncio.Redis client."""
    redis = AsyncMock()
    redis.eval = AsyncMock()
    redis.zcard = AsyncMock()
    redis.zrange = AsyncMock()
    return redis


@pytest.fixture
def _patch_get_redis(mock_redis_client: AsyncMock) -> None:
    """Patch get_redis() to return the mocked Redis client for every test."""
    with patch(
        "app.core.rate_limit.get_redis",
        AsyncMock(return_value=mock_redis_client),
    ):
        yield


@pytest.mark.asyncio
async def test_allowed_returns_true_and_remaining(
    mock_redis_client: AsyncMock,
    _patch_get_redis: None,
) -> None:
    """When the Lua script returns 0 the request is allowed."""
    mock_redis_client.eval.return_value = 0
    mock_redis_client.zcard.return_value = 3

    limiter = RateLimiter(requests_per_minute=10, window_seconds=60, key_prefix="t")
    allowed, remaining, retry_after = await limiter.check_rate_limit("id-a")

    assert allowed is True
    assert remaining == 7  # limit - count = 10 - 3
    assert retry_after == 0


@pytest.mark.asyncio
async def test_remaining_never_negative(
    mock_redis_client: AsyncMock,
    _patch_get_redis: None,
) -> None:
    """Remaining is clamped to zero when zcard exceeds the limit."""
    mock_redis_client.eval.return_value = 0
    mock_redis_client.zcard.return_value = 15  # above limit

    limiter = RateLimiter(requests_per_minute=10, window_seconds=60, key_prefix="t")
    _allowed, remaining, _retry = await limiter.check_rate_limit("id-b")

    assert remaining == 0


@pytest.mark.asyncio
async def test_denied_returns_false_and_retry_after(
    mock_redis_client: AsyncMock,
    _patch_get_redis: None,
) -> None:
    """When the Lua script returns 1 the request is rate-limited."""
    now = time.time()
    oldest = now - 30  # entry inserted 30 s ago

    mock_redis_client.eval.return_value = 1
    mock_redis_client.zrange.return_value = [("m:a", oldest)]

    limiter = RateLimiter(requests_per_minute=10, window_seconds=60, key_prefix="t")
    allowed, remaining, retry_after = await limiter.check_rate_limit("id-c")

    assert allowed is False
    assert remaining == 0
    # Retry is calculated as int(oldest + window - now_internal) + 1.
    # The method uses its own time.time() call, so retry_after may differ
    # from a test-side calculation by 1.
    expected_retry = int(oldest + 60 - now) + 1
    assert retry_after in (expected_retry, expected_retry - 1)


@pytest.mark.asyncio
async def test_denied_falls_back_to_window_when_zrange_empty(
    mock_redis_client: AsyncMock,
    _patch_get_redis: None,
) -> None:
    """When zrange returns nothing, retry_after defaults to the full window."""
    mock_redis_client.eval.return_value = 1
    mock_redis_client.zrange.return_value = []

    limiter = RateLimiter(requests_per_minute=10, window_seconds=60, key_prefix="t")
    _allowed, _remaining, retry_after = await limiter.check_rate_limit("id-d")

    assert retry_after == 60


@pytest.mark.asyncio
async def test_retry_after_minimum_one(
    mock_redis_client: AsyncMock,
    _patch_get_redis: None,
) -> None:
    """retry_after is clamped to 1 even when the computation yields 0."""
    now = time.time()
    oldest = now - 60.9  # expires almost immediately

    mock_redis_client.eval.return_value = 1
    mock_redis_client.zrange.return_value = [("m:x", oldest)]

    limiter = RateLimiter(requests_per_minute=10, window_seconds=60, key_prefix="t")
    _allowed, _remaining, retry_after = await limiter.check_rate_limit("id-e")

    assert retry_after >= 1


@pytest.mark.asyncio
async def test_eval_receives_correct_script(
    mock_redis_client: AsyncMock,
    _patch_get_redis: None,
) -> None:
    """The Lua script passed to eval must be the class-level script."""
    mock_redis_client.eval.return_value = 0
    mock_redis_client.zcard.return_value = 0

    limiter = RateLimiter(requests_per_minute=10, window_seconds=60, key_prefix="t")
    await limiter.check_rate_limit("id-f")

    args, _ = mock_redis_client.eval.call_args
    assert args[0] == RateLimiter._RATE_LIMIT_SCRIPT


@pytest.mark.asyncio
async def test_eval_receives_one_key(
    mock_redis_client: AsyncMock,
    _patch_get_redis: None,
) -> None:
    """The numkeys argument must be 1."""
    mock_redis_client.eval.return_value = 0
    mock_redis_client.zcard.return_value = 0

    limiter = RateLimiter(requests_per_minute=10, window_seconds=60, key_prefix="t")
    await limiter.check_rate_limit("id-g")

    args, _ = mock_redis_client.eval.call_args
    assert args[1] == 1


@pytest.mark.asyncio
async def test_eval_receives_namespaced_key(
    mock_redis_client: AsyncMock,
    _patch_get_redis: None,
) -> None:
    """The Redis key passed to eval must include the configured prefix."""
    mock_redis_client.eval.return_value = 0
    mock_redis_client.zcard.return_value = 0

    limiter = RateLimiter(requests_per_minute=10, window_seconds=60, key_prefix="app")
    await limiter.check_rate_limit("user-99")

    args, _ = mock_redis_client.eval.call_args
    assert args[2] == "app:user-99"


@pytest.mark.asyncio
async def test_eval_receives_window_and_limit(
    mock_redis_client: AsyncMock,
    _patch_get_redis: None,
) -> None:
    """The window and max_requests values must be forwarded to the script."""
    mock_redis_client.eval.return_value = 0
    mock_redis_client.zcard.return_value = 0

    limiter = RateLimiter(requests_per_minute=25, window_seconds=120, key_prefix="rl")
    await limiter.check_rate_limit("id-h")

    args, _ = mock_redis_client.eval.call_args
    # args layout: (script, numkeys, key, now, window, limit, member)
    assert str(120) in args
    assert str(25) in args


# check_user_rate_limit() / check_api_key_rate_limit()

# These tests use the original function references captured at import time
# (before the autouse disable_rate_limit fixture monkeypatches them).
# We control behaviour by patching check_rate_limit on the global limiter
# instances.


@pytest.mark.asyncio
async def test_check_user_rate_limit_allowed() -> None:
    """When the rate limiter allows, no exception is raised."""
    with patch.object(
        _rl_mod.user_rate_limiter,
        "check_rate_limit",
        AsyncMock(return_value=(True, 5, 0)),
    ):
        # Should not raise
        await _original_check_user("user-1")


@pytest.mark.asyncio
async def test_check_user_rate_limit_exceeded() -> None:
    """When the rate limiter denies, RateLimitExceeded is raised."""
    with patch.object(
        _rl_mod.user_rate_limiter,
        "check_rate_limit",
        AsyncMock(return_value=(False, 0, 42)),
    ):
        with pytest.raises(RateLimitExceeded) as exc_info:
            await _original_check_user("user-2")
        assert exc_info.value.status_code == 429
        assert exc_info.value.headers == {"Retry-After": "42"}


@pytest.mark.asyncio
async def test_check_user_rate_limit_forwards_identifier() -> None:
    """The user identifier should be forwarded to the limiter."""
    mock_check = AsyncMock(return_value=(True, 5, 0))
    with patch.object(_rl_mod.user_rate_limiter, "check_rate_limit", mock_check):
        await _original_check_user("user-abc-123")
        mock_check.assert_awaited_once_with("user-abc-123")


@pytest.mark.asyncio
async def test_check_api_key_rate_limit_allowed() -> None:
    """When the rate limiter allows the API key, no exception is raised."""
    with patch.object(
        _rl_mod.api_key_rate_limiter,
        "check_rate_limit",
        AsyncMock(return_value=(True, 5, 0)),
    ):
        await _original_check_api_key("key-1")


@pytest.mark.asyncio
async def test_check_api_key_rate_limit_exceeded() -> None:
    """When the rate limiter denies the API key, RateLimitExceeded is raised."""
    with patch.object(
        _rl_mod.api_key_rate_limiter,
        "check_rate_limit",
        AsyncMock(return_value=(False, 0, 10)),
    ):
        with pytest.raises(RateLimitExceeded) as exc_info:
            await _original_check_api_key("key-2")
        assert exc_info.value.status_code == 429
        assert exc_info.value.headers == {"Retry-After": "10"}


@pytest.mark.asyncio
async def test_check_api_key_rate_limit_forwards_identifier() -> None:
    """The API key identifier should be forwarded to the limiter."""
    mock_check = AsyncMock(return_value=(True, 5, 0))
    with patch.object(_rl_mod.api_key_rate_limiter, "check_rate_limit", mock_check):
        await _original_check_api_key("api-key-xyz-789")
        mock_check.assert_awaited_once_with("api-key-xyz-789")
