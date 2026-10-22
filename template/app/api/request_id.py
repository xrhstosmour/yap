"""Request ID middleware for distributed request tracing.

Generates or propagates unique request IDs across services,
enabling end-to-end request tracking in logs and traces.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

import structlog
from starlette.datastructures import Headers
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp
from starlette.types import Message
from starlette.types import Receive
from starlette.types import Scope
from starlette.types import Send

request_id_context: ContextVar[str] = ContextVar("request_id", default="")

# Mirrors the `_KEY_PATTERN` idempotency keys are validated against
# (`app/api/idempotency.py`): 8-64 chars of `[\w-]`. A client-supplied
# `X-Request-ID` that doesn't match this is untrusted input, replaced
# with a freshly generated ID rather than logged and echoed verbatim.
_REQUEST_ID_PATTERN = re.compile(r"^[\w\-]{8,64}$")


class RequestIDMiddleware:
    """Middleware that assigns or propagates a request ID per request.

    Reads X-Request-ID from incoming requests, or generates a new UUID.
    The request ID is stored in a context variable, bound to structlog's
    contextvars so every log line emitted during the request carries the
    same `correlation_id`, and appended to the response headers.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Extract or generate request ID, store in context, and return in response.

        Args:
            scope: The ASGI connection scope.
            receive: The ASGI receive channel.
            send: The ASGI send channel, whose response gets X-Request-ID.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = Headers(scope=scope).get("X-Request-ID")
        if not request_id or not _REQUEST_ID_PATTERN.match(request_id):
            request_id = str(uuid.uuid7())

        request_id_context.set(request_id)

        # Bound alongside `merge_contextvars`, the first processor in
        # `setup_logging()`, so every log call made while handling this
        # request (directly or via `get_logger()` elsewhere) picks up the
        # same `correlation_id` as the `X-Request-ID` response header,
        # instead of `add_correlation_id` minting an unrelated UUID per
        # log call.
        structlog.contextvars.bind_contextvars(correlation_id=request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            structlog.contextvars.unbind_contextvars("correlation_id")


def get_request_id() -> str:
    """Return the request ID for the current request context.

    Returns:
        Request ID string, or empty string if outside a request context
    """
    return request_id_context.get()
