"""Request ID middleware for distributed request tracing.

Generates or propagates unique request IDs across services,
enabling end-to-end request tracking in logs and traces.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.base import RequestResponseEndpoint

request_id_context: ContextVar[str] = ContextVar("request_id", default="")

# Mirrors the `_KEY_PATTERN` idempotency keys are validated against
# (`app/api/idempotency.py`): 8-64 chars of `[\w-]`. A client-supplied
# `X-Request-ID` that doesn't match this is untrusted input, replaced
# with a freshly generated ID rather than logged and echoed verbatim.
_REQUEST_ID_PATTERN = re.compile(r"^[\w\-]{8,64}$")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware that assigns or propagates a request ID per request.

    Reads X-Request-ID from incoming requests, or generates a new UUID.
    The request ID is stored in a context variable, bound to structlog's
    contextvars so every log line emitted during the request carries the
    same `correlation_id`, and appended to the response headers.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Extract or generate request ID, store in context, and return in response.

        Args:
            request: Incoming Starlette request
            call_next: Next middleware or endpoint handler

        Returns:
            Starlette response with X-Request-ID header
        """
        request_id = request.headers.get("X-Request-ID")
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
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("correlation_id")
        response.headers["X-Request-ID"] = request_id

        return response


def get_request_id() -> str:
    """Return the request ID for the current request context.

    Returns:
        Request ID string, or empty string if outside a request context
    """
    return request_id_context.get()
