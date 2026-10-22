"""Per-request context and logging middleware.

Plain ASGI rather than `@app.middleware("http")`, which wraps each one in
Starlette's `BaseHTTPMiddleware`: that runs the rest of the stack in a
separate task and streams every response through a memory channel, a cost
paid once per layer on every request.
"""

from __future__ import annotations

import time

from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import ASGIApp
from starlette.types import Message
from starlette.types import Receive
from starlette.types import Scope
from starlette.types import Send

from app import database
from app.core.logging import get_logger
from app.core.tenant import tenant_context
from app.database import ADDITIONAL_DATABASE_MODE
from app.database import database_mode_variable

# The name the request log lines were always written under.
logger = get_logger("app")


class DatabaseModeMiddleware:
    """Route the request to the database its `X-Database-Mode` names.

    Sets `database_mode_variable`, which `get_async_session()` reads to use
    the additional database when the mode is `additional`. Unknown modes are
    rejected with 400 so callers get immediate feedback instead of silently
    routing to the default database.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw = Headers(scope=scope).get("X-Database-Mode", "")
        mode = raw.strip().lower() if raw else ""
        if mode and mode != ADDITIONAL_DATABASE_MODE:
            response = Response(f"Unknown database mode: {mode}", status_code=400)
            await response(scope, receive, send)
            return
        # Refused here rather than left to `get_async_session()`, whose
        # `RuntimeError` reached anyone who sent the header as a 500.
        if mode and database.additional_async_session_factory is None:
            response = Response(
                "The additional database is not configured", status_code=400
            )
            await response(scope, receive, send)
            return

        token = database_mode_variable.set(mode)
        try:
            await self.app(scope, receive, send)
        finally:
            database_mode_variable.reset(token)


class TenantContextMiddleware:
    """Scope the tenant context to one request.

    This looks like a no-op and is not. The tenant is set by
    `get_current_user`, a dependency, which runs downstream of every
    middleware, so there is never one to read here. What matters is the
    `with` block: it scopes the tenant ContextVar to this request and
    restores it afterwards. Without it, the tenant set by one request's
    dependency stays set for the next request handled on the same task, so
    an unauthenticated request inherits the previous caller's tenant.
    `tests/core/test_tenant.py` covers exactly that.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        with tenant_context(scope.get("state", {}).get("tenant_id")):
            await self.app(scope, receive, send)


class RequestLoggingMiddleware:
    """Log every request with its status and timing."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.perf_counter()
        status_code = 500

        async def send_with_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        await self.app(scope, receive, send_with_status)

        logger.info(
            "request_completed",
            method=scope["method"],
            path=scope["path"],
            status_code=status_code,
            duration_ms=round((time.perf_counter() - start_time) * 1000, 2),
        )
