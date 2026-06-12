"""Idempotency middleware powered by `X-Idempotency-Key`.

Clients that include an `X-Idempotency-Key` header on mutating
requests (POST, PATCH, PUT, DELETE) are guaranteed that retrying the
same key will not execute the side effect a second time — the cached
response is replayed instead.
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING
from typing import Any
from typing import cast

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

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

        # Derive user scope from auth token to prevent cross-user collisions.
        auth = request.headers.get("Authorization")
        user_scope = hashlib.sha256(auth.encode()).hexdigest()[:16] if auth else "anon"
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

        locked = await idempotency_service.try_lock(scoped_key)
        if not locked:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "Request already in progress for this idempotency key"
                },
            )

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
            await idempotency_service.release_lock(scoped_key)

    @staticmethod
    async def _buffer_and_cache(
        raw_key: str, response: Response
    ) -> tuple[bytes | None, bool]:
        if response.status_code >= 500:
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
