"""Rate limiting implementation using Redis sliding window.

This module provides rate limiting functionality to prevent abuse.
Uses a sliding window algorithm for accurate rate limiting across
distributed instances.
"""

from __future__ import annotations

import secrets
import time

from fastapi import HTTPException
from fastapi import status

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

    The algorithm:
    1. Remove expired entries from the window
    2. Count remaining entries
    3. If under limit, add new entry
    4. Return remaining count and retry time
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

        Uses Redis MULTI/EXEC for atomic operations to ensure
        accurate counting across concurrent requests.

        Args:
            identifier: Unique identifier (e.g., user_id, api_key, IP)

        Returns:
            Tuple of (allowed, remaining, retry_after)
            - allowed: True if request is allowed
            - remaining: Remaining requests in window
            - retry_after: Seconds until oldest entry expires (0 if allowed)
        """
        redis_client = await get_redis()
        key = self._make_key(identifier)
        now = time.time()
        window_start = now - self.window

        # Use pipeline for atomic operations.
        pipe = redis_client.pipeline()

        # Remove old entries outside the window.
        pipe.zremrangebyscore(key, 0, window_start)

        # Count current entries in window.
        pipe.zcard(key)

        # Execute both commands.
        results = await pipe.execute()
        current_count = results[1]

        if current_count < self.limit:
            # Under limit - add new entry.
            pipe2 = redis_client.pipeline()
            pipe2.zadd(key, {f"{now}:{secrets.token_hex(4)}": now})
            pipe2.expire(key, self.window)
            await pipe2.execute()

            remaining = self.limit - current_count - 1
            return True, remaining, 0
        else:
            # Over limit - calculate retry time.
            oldest_entries = await redis_client.zrange(key, 0, 0, withscores=True)
            if oldest_entries:
                oldest_time = oldest_entries[0][1]
                retry_after = int(oldest_time + self.window - now) + 1
            else:
                retry_after = self.window

            return False, 0, max(1, retry_after)


# Global rate limiters for different contexts.
user_rate_limiter = RateLimiter(
    requests_per_minute=settings.RATE_LIMIT_PER_MINUTE,
    key_prefix="ratelimit:user",
)

api_key_rate_limiter = RateLimiter(
    requests_per_minute=settings.RATE_LIMIT_PER_MINUTE_API_KEY,
    key_prefix="ratelimit:api_key",
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
