"""Request ID middleware for distributed request tracing.

Generates or propagates unique request IDs across services,
enabling end-to-end request tracking in logs and traces.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.base import RequestResponseEndpoint

request_id_context: ContextVar[str] = ContextVar("request_id", default="")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware that assigns or propagates a request ID per request.

    Reads X-Request-ID from incoming requests, or generates a new UUID.
    The request ID is stored in a context variable and appended to the
    response headers.
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
        if not request_id:
            request_id = str(uuid.uuid7())

        request_id_context.set(request_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id

        return response


def get_request_id() -> str:
    """Return the request ID for the current request context.

    Returns:
        Request ID string, or empty string if outside a request context
    """
    return request_id_context.get()
