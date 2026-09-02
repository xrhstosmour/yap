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
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

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
    """When the Lua script returns allowed the request is permitted."""
    mock_redis_client.eval.return_value = [0, 7, 0]  # allowed, 7 remaining, 0 retry

    limiter = RateLimiter(requests_per_minute=10, window_seconds=60, key_prefix="t")
    allowed, remaining, retry_after = await limiter.check_rate_limit("id-a")

    assert allowed is True
    assert remaining == 7
    assert retry_after == 0


@pytest.mark.asyncio
async def test_remaining_never_negative(
    mock_redis_client: AsyncMock,
    _patch_get_redis: None,
) -> None:
    """Remaining is clamped to zero when the Lua count exceeds the limit."""
    mock_redis_client.eval.return_value = [0, -6, 0]  # Lua returned negative remaining

    limiter = RateLimiter(requests_per_minute=10, window_seconds=60, key_prefix="t")
    _allowed, remaining, _retry = await limiter.check_rate_limit("id-b")

    assert remaining == 0


@pytest.mark.asyncio
async def test_denied_returns_false_and_retry_after(
    mock_redis_client: AsyncMock,
    _patch_get_redis: None,
) -> None:
    """When the Lua script returns denied the request is rate-limited."""
    now = time.time()
    oldest = now - 30  # entry inserted 30 s ago
    retry_seconds = int(oldest + 60 - now)  # Lua computes oldest + window - now

    mock_redis_client.eval.return_value = [1, 0, retry_seconds]

    limiter = RateLimiter(requests_per_minute=10, window_seconds=60, key_prefix="t")
    allowed, remaining, retry_after = await limiter.check_rate_limit("id-c")

    assert allowed is False
    assert remaining == 0
    assert retry_after == retry_seconds + 1


@pytest.mark.asyncio
async def test_denied_falls_back_to_window_when_no_oldest(
    mock_redis_client: AsyncMock,
    _patch_get_redis: None,
) -> None:
    """When Lua has no oldest entry it returns the full window as retry."""
    mock_redis_client.eval.return_value = [1, 0, 60]  # window fallback

    limiter = RateLimiter(requests_per_minute=10, window_seconds=60, key_prefix="t")
    _allowed, _remaining, retry_after = await limiter.check_rate_limit("id-d")

    assert retry_after == 61  # int(60) + 1


@pytest.mark.asyncio
async def test_retry_after_minimum_one(
    mock_redis_client: AsyncMock,
    _patch_get_redis: None,
) -> None:
    """retry_after is clamped to 1 even when the Lua computation yields 0."""
    mock_redis_client.eval.return_value = [1, 0, 0]  # retry_after_raw = 0

    limiter = RateLimiter(requests_per_minute=10, window_seconds=60, key_prefix="t")
    _allowed, _remaining, retry_after = await limiter.check_rate_limit("id-e")

    assert retry_after >= 1


@pytest.mark.asyncio
async def test_eval_receives_correct_script(
    mock_redis_client: AsyncMock,
    _patch_get_redis: None,
) -> None:
    """The Lua script passed to eval must be the class-level script."""
    mock_redis_client.eval.return_value = [0, 9, 0]

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
    mock_redis_client.eval.return_value = [0, 9, 0]

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
    mock_redis_client.eval.return_value = [0, 9, 0]

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
    mock_redis_client.eval.return_value = [0, 24, 0]

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


class TestRateLimiterFailsOpen:
    """A Redis outage must not take the authenticated API down with it.

    `check_user_rate_limit` runs on every authenticated request and had no
    error handling, so a Redis blip surfaced as a 500 across the whole API.
    Revocation already fails open for exactly this reason, see
    `is_token_blacklisted`.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [
            RedisConnectionError("redis is down"),
            RedisTimeoutError("redis timed out"),
        ],
    )
    async def test_a_failing_command_allows_the_request(self, error: Exception) -> None:
        """Redis is reachable but the command fails.

        Args:
            error: A failure the limiter may hit running its script.
        """
        broken = AsyncMock()
        broken.eval = AsyncMock(side_effect=error)

        with patch("app.core.rate_limit.get_redis", AsyncMock(return_value=broken)):
            await _original_check_user("user-1")

    @pytest.mark.asyncio
    async def test_an_unavailable_client_allows_the_request(self) -> None:
        """Redis cannot be reached at all, so `get_redis` itself raises."""
        with patch(
            "app.core.rate_limit.get_redis",
            AsyncMock(side_effect=RuntimeError("Redis client not initialized")),
        ):
            await _original_check_user("user-1")

    @pytest.mark.asyncio
    async def test_the_auth_limiter_fails_open_too(self) -> None:
        """Sign-in must not become impossible during a Redis outage."""
        from fastapi import Request

        from app.core.rate_limit import check_auth_rate_limit

        broken = AsyncMock()
        broken.eval = AsyncMock(side_effect=RedisConnectionError("redis is down"))

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/auth/login",
                "headers": [],
                "client": ("203.0.113.5", 1234),
            }
        )

        with patch("app.core.rate_limit.get_redis", AsyncMock(return_value=broken)):
            await check_auth_rate_limit(request)

    @pytest.mark.asyncio
    async def test_a_reachable_redis_still_limits(self) -> None:
        """Failing open must not mean never limiting."""
        from app.core.rate_limit import RateLimiter

        limiter = RateLimiter(requests_per_minute=1, key_prefix="ratelimit:test")

        over_limit = AsyncMock()
        over_limit.eval = AsyncMock(return_value=[1, 0, 30])

        with patch("app.core.rate_limit.get_redis", AsyncMock(return_value=over_limit)):
            allowed, remaining, retry_after = await limiter.check_rate_limit("user-1")

        assert allowed is False
        assert remaining == 0
        assert retry_after > 0
