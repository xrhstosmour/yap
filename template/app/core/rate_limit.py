"""Rate limiting implementation using Redis sliding window.

This module provides rate limiting functionality to prevent abuse.
Uses a sliding window algorithm for accurate rate limiting across
distributed instances.
"""

from __future__ import annotations

import secrets
import time

from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.cache import get_redis
from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger("rate_limit")


class RateLimitExceeded(HTTPException):
    """Exception raised when rate limit is exceeded.

    Returns HTTP 429 Too Many Requests with retry information.
    """

    def __init__(self, retry_after: int) -> None:
        """Initialize rate limit exception.

        Args:
            retry_after: Seconds until the rate limit resets
        """
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )


class RateLimiter:
    """Redis-based sliding window rate limiter.

    Uses Redis sorted sets to implement a sliding window rate limiter.
    This provides accurate rate limiting across multiple server instances
    while minimizing Redis memory usage.

    The algorithm (executed as an atomic Lua script):
    1. Remove expired entries from the window
    2. Count remaining entries
    3. If under limit, add new entry and return 0 (allowed)
    4. If over limit, return 1 (rate limited)
    """

    _RATE_LIMIT_SCRIPT = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local window = tonumber(ARGV[2])
    local max_requests = tonumber(ARGV[3])
    local member = ARGV[4]

    redis.call("ZREMRANGEBYSCORE", key, 0, now - window)
    local count = redis.call("ZCARD", key)
    if count < max_requests then
        redis.call("ZADD", key, now, member)
        redis.call("EXPIRE", key, window)
        return {0, max_requests - count - 1, 0}
    end
    local oldest = redis.call("ZRANGE", key, 0, 0, "WITHSCORES")
    local retry_after = oldest[2] and (oldest[2] + window - now) or window
    return {1, 0, retry_after}
    """

    def __init__(
        self,
        requests_per_minute: int,
        window_seconds: int = 60,
        key_prefix: str = "ratelimit",
    ) -> None:
        """Initialize rate limiter.

        Args:
            requests_per_minute: Maximum requests allowed per window
            window_seconds: Window size in seconds (default: 60)
            key_prefix: Redis key prefix for namespacing
        """
        self.limit = requests_per_minute
        self.window = window_seconds
        self.key_prefix = key_prefix

    def _make_key(self, identifier: str) -> str:
        """Create Redis key for rate limit counter."""
        return f"{self.key_prefix}:{identifier}"

    async def check_rate_limit(self, identifier: str) -> tuple[bool, int, int]:
        """Check if request is within rate limit.

        Uses an atomic Redis Lua script to eliminate the race condition
        between removal, counting, and insertion across concurrent requests.

        Args:
            identifier: Unique identifier (e.g., user_id, api_key, IP)

        Returns:
            Tuple of (allowed, remaining, retry_after)
            - allowed: True if request is allowed
            - remaining: Remaining requests in window
            - retry_after: Seconds until oldest entry expires (0 if allowed)
        """
        key = self._make_key(identifier)
        now = time.time()
        member = f"{now}:{secrets.token_hex(4)}"

        try:
            redis_client = await get_redis()
            result = await redis_client.eval(  # type: ignore[misc]
                self._RATE_LIMIT_SCRIPT,
                1,
                key,
                str(now),
                str(self.window),
                str(self.limit),
                member,
            )
        except (RedisConnectionError, RedisTimeoutError, RedisError, RuntimeError):
            # Fail open, the same stance `is_token_blacklisted` takes and for
            # the same reason: this runs on every authenticated request, so a
            # Redis blip used to turn into a 500 across the whole API. An
            # unthrottled window during an outage is the smaller problem, and
            # authentication itself is unaffected.
            logger.warning("rate_limit_unavailable", key=key, exc_info=True)
            return True, self.limit, 0

        allowed_flag, remaining_raw, retry_after_raw = result[0], result[1], result[2]
        if allowed_flag == 0:
            return True, max(0, int(remaining_raw)), 0
        else:
            return False, 0, max(1, int(retry_after_raw) + 1)


# Global rate limiters for different contexts.
user_rate_limiter = RateLimiter(
    requests_per_minute=settings.RATE_LIMIT_PER_MINUTE,
    key_prefix="ratelimit:user",
)

api_key_rate_limiter = RateLimiter(
    requests_per_minute=settings.RATE_LIMIT_PER_MINUTE_API_KEY,
    key_prefix="ratelimit:api_key",
)

auth_rate_limiter = RateLimiter(
    requests_per_minute=settings.RATE_LIMIT_PER_MINUTE_AUTH,
    key_prefix="ratelimit:auth",
)


async def check_user_rate_limit(user_id: str) -> None:
    """Check if user has exceeded their rate limit.

    Args:
        user_id: User identifier

    Raises:
        RateLimitExceeded: If rate limit is exceeded
    """
    allowed, remaining, retry_after = await user_rate_limiter.check_rate_limit(user_id)

    if not allowed:
        logger.warning("rate_limit_exceeded", user_id=user_id, retry_after=retry_after)
        raise RateLimitExceeded(retry_after)


async def check_api_key_rate_limit(api_key_id: str) -> None:
    """Check if API key has exceeded its rate limit.

    Args:
        api_key_id: API key identifier

    Raises:
        RateLimitExceeded: If rate limit is exceeded
    """
    allowed, remaining, retry_after = await api_key_rate_limiter.check_rate_limit(
        api_key_id
    )

    if not allowed:
        logger.warning(
            "api_key_rate_limit_exceeded",
            api_key_id=api_key_id,
            retry_after=retry_after,
        )
        raise RateLimitExceeded(retry_after)


async def check_auth_rate_limit(request: Request) -> None:
    """Check whether a client IP has exceeded the unauthenticated-auth rate limit.

    Applied to endpoints that accept unauthenticated requests (login, register,
    password-reset, magic-link) where the standard post-auth rate limiters
    (`check_user_rate_limit`, `check_api_key_rate_limit`) never run.

    Args:
        request: Incoming request, used to key the limiter on client IP

    Raises:
        RateLimitExceeded: If rate limit is exceeded
    """
    client_ip = request.client.host if request.client else "unknown"
    allowed, remaining, retry_after = await auth_rate_limiter.check_rate_limit(
        client_ip
    )

    if not allowed:
        logger.warning(
            "auth_rate_limit_exceeded",
            client_ip=client_ip,
            retry_after=retry_after,
        )
        raise RateLimitExceeded(retry_after)
