"""Idempotency service for duplicate request detection.

Stores completed response data keyed by `X-Idempotency-Key` so that
retrying a mutating request returns the original result instead of
executing the side effect again.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import cast

from app.core.cache import get_redis
from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger("idempotency")

LOCK_TTL_SECONDS = 60


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

    async def try_lock(self, raw_key: str) -> bool:
        """Atomically claim the processing lock for *raw_key*.

        Returns `True` if this caller acquired the lock (first
        request for this key).  Returns `False` if another request
        is already processing the same key.
        """
        try:
            r = await get_redis()
            return cast(
                bool,
                await r.set(self._lock_key(raw_key), "1", nx=True, ex=LOCK_TTL_SECONDS),
            )
        except Exception:
            logger.exception("idempotency_lock_failed")
            raise

    async def release_lock(self, raw_key: str) -> None:
        """Remove the processing lock for *raw_key*."""
        try:
            r = await get_redis()
            await r.delete(self._lock_key(raw_key))
        except Exception:
            logger.exception("idempotency_unlock_failed")


# Global singleton.
idempotency_service = IdempotencyService()
