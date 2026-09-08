"""Idempotency service for duplicate request detection.

Stores completed response data keyed by `X-Idempotency-Key` so that
retrying a mutating request returns the original result instead of
executing the side effect again.
"""

from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from typing import cast

from app.core.cache import get_redis
from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger("idempotency")

LOCK_TTL_SECONDS = 60

# The lock is refreshed at this interval while the guarded request is
# still in flight (see `IdempotencyMiddleware`'s heartbeat task), well
# inside `LOCK_TTL_SECONDS`, so a slow handler doesn't outlive its own
# lock: a request that takes longer than `LOCK_TTL_SECONDS` to process
# used to have its lock expire mid-flight, letting a retry with the same
# idempotency key slip past `try_lock()` and execute the same side
# effect a second time concurrently with the first.
LOCK_HEARTBEAT_INTERVAL_SECONDS = 20


@dataclass
class CachedResponse:
    body: bytes
    status_code: int
    media_type: str | None
    headers: dict[str, str] | None = None


class IdempotencyService:
    """Redis-backed idempotency storage.

    Keys are namespaced under `idempotency:` and expire after
    `settings.IDEMPOTENCY_TTL_HOURS`.
    """

    def __init__(self) -> None:
        self._prefix = "idempotency"

    def _key(self, raw: str) -> str:
        return f"{self._prefix}:{raw}"

    def _lock_key(self, raw: str) -> str:
        return f"{self._prefix}:lock:{raw}"

    def _serialize(self, resp: CachedResponse) -> bytes:
        return json.dumps(
            {
                "b": base64.b64encode(resp.body).decode(),
                "s": resp.status_code,
                "m": resp.media_type,
                "h": resp.headers,
            }
        ).encode()

    def _deserialize(self, raw: bytes) -> CachedResponse | None:
        try:
            data = json.loads(raw)
            return CachedResponse(
                body=base64.b64decode(data["b"]),
                status_code=data["s"],
                media_type=data.get("m"),
                headers=data.get("h"),
            )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("corrupt_idempotency_cache", error=str(exc))
            return None

    async def get(self, raw_key: str) -> CachedResponse | None:
        """Return cached response for *raw_key*, or `None`."""
        try:
            r = await get_redis()
            data = await r.get(self._key(raw_key))
            if data is not None:
                return self._deserialize(data)
        except Exception:
            logger.exception("idempotency_get_failed")
        return None

    async def set(self, raw_key: str, resp: CachedResponse) -> None:
        """Store *resp* for *raw_key* with the configured TTL."""
        try:
            r = await get_redis()
            await r.setex(
                self._key(raw_key),
                int(timedelta(hours=settings.IDEMPOTENCY_TTL_HOURS).total_seconds()),
                self._serialize(resp),
            )
        except Exception:
            logger.exception("idempotency_set_failed")

    async def try_lock(self, raw_key: str) -> str | None:
        """Atomically claim the processing lock for *raw_key*.

        Returns a random ownership token if this caller acquired the lock
        (first request for this key), the same token must be passed to
        `extend_lock()`/`release_lock()` so a request whose lock already
        expired and was re-acquired by a retry can't extend or delete the
        retry's lock out from under it. Returns `None` if another request
        is already processing the same key.
        """
        try:
            r = await get_redis()
            token = secrets.token_urlsafe(16)
            acquired = await r.set(
                self._lock_key(raw_key), token, nx=True, ex=LOCK_TTL_SECONDS
            )
            return token if acquired else None
        except Exception:
            logger.exception("idempotency_lock_failed")
            raise

    async def release_lock(self, raw_key: str, token: str) -> None:
        """Remove the processing lock for *raw_key*, if still owned by *token*.

        A plain unconditional `DEL` would remove whatever lock currently
        occupies the key, including one a retry acquired after this
        caller's own lock already expired, releasing a request that
        isn't this one's to release. Compare-and-delete via a single Lua
        script keeps the check and the delete atomic.
        """
        try:
            r = await get_redis()
            await cast(
                Any,
                r.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then "
                    "return redis.call('del', KEYS[1]) else return 0 end",
                    1,
                    self._lock_key(raw_key),
                    token,
                ),
            )
        except Exception:
            logger.exception("idempotency_unlock_failed")

    async def extend_lock(self, raw_key: str, token: str) -> None:
        """Refresh the processing lock's TTL for *raw_key*, if still owned by
        *token*.

        Called periodically by the middleware's heartbeat while the
        guarded request is still being handled, so a request that runs
        longer than `LOCK_TTL_SECONDS` doesn't have its lock expire out
        from under it. Compare-and-expire via a single Lua script, for
        the same reason `release_lock()` compares before deleting: an
        unconditional `EXPIRE` would refresh whatever lock currently
        occupies the key, not necessarily this caller's own. A failure
        here is logged but not raised: losing one heartbeat still leaves
        `LOCK_TTL_SECONDS` of headroom before the lock actually expires,
        for the next heartbeat to recover in.
        """
        try:
            r = await get_redis()
            await cast(
                Any,
                r.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then "
                    "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end",
                    1,
                    self._lock_key(raw_key),
                    token,
                    str(LOCK_TTL_SECONDS),
                ),
            )
        except Exception:
            logger.exception("idempotency_lock_extend_failed")


# Global singleton.
idempotency_service = IdempotencyService()
