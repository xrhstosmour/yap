"""Idempotency middleware powered by `X-Idempotency-Key`.

Clients that include an `X-Idempotency-Key` header on mutating
requests (POST, PATCH, PUT, DELETE) are guaranteed that retrying the
same key will not execute the side effect a second time, the cached
response is replayed instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import re
from typing import TYPE_CHECKING
from typing import Any
from typing import cast

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.core.idempotency import LOCK_HEARTBEAT_INTERVAL_SECONDS
from app.core.idempotency import CachedResponse
from app.core.idempotency import idempotency_service
from app.core.logging import get_logger

if TYPE_CHECKING:
    from starlette.requests import Request

logger = get_logger("idempotency")

_KEY_PATTERN = re.compile(r"^[\w\-]{8,64}$")
_MUTABLE_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Middleware that enforces idempotent request processing.

    Place this middleware **after** authentication / tenant middleware
    so any user context is already available in `request.state`.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method not in _MUTABLE_METHODS:
            return await call_next(request)

        raw_key = request.headers.get("X-Idempotency-Key")
        if not raw_key:
            return await call_next(request)

        # Validate format, reject early.
        if not _KEY_PATTERN.match(raw_key):
            return JSONResponse(
                status_code=422,
                content={
                    "detail": "X-Idempotency-Key must be 8-64 chars of [\\w\\-]",
                },
            )

        # Derive user scope from the caller's credential to prevent
        # cross-user collisions. API-key auth is included alongside bearer
        # tokens, and unauthenticated callers are scoped by client address
        # rather than a single shared "anon" bucket, or two different
        # anonymous clients presenting the same key (trivially guessable,
        # since the format allows something like "00000000") would each
        # replay the other's cached response, including any tokens it
        # contains (e.g. on /auth/login, /auth/register).
        auth = request.headers.get("Authorization")
        api_key = request.headers.get("X-API-Key")
        if auth:
            user_scope = hashlib.sha256(auth.encode()).hexdigest()
        elif api_key:
            user_scope = hashlib.sha256(api_key.encode()).hexdigest()
        else:
            client_host = request.client.host if request.client else "unknown"
            user_scope = f"anon:{client_host}"
        scoped_key = f"{user_scope}:{request.method}:{request.url.path}:{raw_key}"

        cached = await idempotency_service.get(scoped_key)
        if cached is not None:
            logger.info("idempotency_cache_hit", key=scoped_key)
            return Response(
                content=cached.body,
                status_code=cached.status_code,
                media_type=cached.media_type or "application/json",
                headers=cached.headers,
            )

        try:
            token = await idempotency_service.try_lock(scoped_key)
        except Exception:
            logger.exception("idempotency_unavailable", key=scoped_key)
            return JSONResponse(
                status_code=503,
                content={"detail": "Idempotency service temporarily unavailable"},
            )
        if token is None:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "Request already in progress for this idempotency key"
                },
            )

        # `LOCK_TTL_SECONDS` bounds how long the lock can outlive a crashed
        # worker, but a slow-but-healthy handler can legitimately run
        # longer than that. This heartbeat keeps the lock alive for as
        # long as `call_next` is actually in flight, refreshing its TTL
        # well before it would otherwise expire and let a retry with the
        # same key slip past `try_lock()` mid-request.
        heartbeat_task = asyncio.create_task(self._heartbeat(scoped_key, token))
        try:
            response = await call_next(request)
            body_bytes, _ = await self._buffer_and_cache(scoped_key, response)
            if body_bytes is not None:
                return Response(
                    content=body_bytes,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type,
                )
            return response
        finally:
            heartbeat_task.cancel()
            # A cancellation delivered to *this* task (e.g. the client
            # disconnected) surfaces here as the same `CancelledError`
            # `await heartbeat_task` raises for the heartbeat's own
            # cancellation, suppressing it unconditionally would also
            # swallow that outer cancellation and let `release_lock`
            # below run to completion instead of the task actually
            # stopping. Only suppress when it's specifically the
            # heartbeat's cancellation being observed.
            current = asyncio.current_task()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
            if current is not None and current.cancelling():
                raise
            await idempotency_service.release_lock(scoped_key, token)

    @staticmethod
    async def _heartbeat(raw_key: str, token: str) -> None:
        """Periodically refresh the idempotency lock's TTL for *raw_key*.

        Runs for the lifetime of the guarded request; cancelled by the
        caller once `call_next` returns (or raises).
        """
        while True:
            await asyncio.sleep(LOCK_HEARTBEAT_INTERVAL_SECONDS)
            await idempotency_service.extend_lock(raw_key, token)

    @staticmethod
    async def _buffer_and_cache(
        raw_key: str, response: Response
    ) -> tuple[bytes | None, bool]:
        # Only successes and redirects are replayable. Caching 4xx too meant
        # a transient rejection stuck to the key for
        # `IDEMPOTENCY_TTL_HOURS` (24 by default): a 429 from the rate
        # limiter, or a 401 from an expired token, was replayed for the rest
        # of the day even once the client had waited or re-authenticated,
        # and a 422 kept being returned after the payload was corrected. The
        # only escape was to change the idempotency key, which is exactly
        # what the key exists to make unnecessary.
        #
        # Nothing is lost by not caching them. Idempotency exists to stop a
        # retry repeating a side effect, and a rejected request did not have
        # one to repeat.
        if response.status_code >= 400:
            return None, False

        body: Any = []
        async for chunk in cast(Any, response).body_iterator:
            body.append(chunk)
        body_bytes = b"".join(body)

        safe_headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower()
            in {"content-type", "content-encoding", "content-language", "location"}
        }

        cached = CachedResponse(
            body=body_bytes,
            status_code=response.status_code,
            media_type=response.media_type,
            headers=safe_headers,
        )
        await idempotency_service.set(raw_key, cached)
        return body_bytes, True
