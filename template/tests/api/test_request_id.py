"""Tests for the request ID middleware and its structlog correlation."""

from __future__ import annotations

import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.request_id import RequestIDMiddleware
from app.api.request_id import request_id_context
from app.core.logging import add_correlation_id


def _app_with_middleware() -> TestClient:
    """Minimal FastAPI app wired with only the request ID middleware."""
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/ping")
    async def ping() -> dict:
        return {
            "request_id_context": request_id_context.get(),
            "correlation_id": structlog.contextvars.get_contextvars().get(
                "correlation_id"
            ),
        }

    return TestClient(app)


class TestRequestIDMiddleware:
    """Behaviour tests for RequestIDMiddleware."""

    def test_generates_request_id_when_missing(self) -> None:
        """No X-Request-ID header should get a generated UUID in the response."""
        client = _app_with_middleware()
        resp = client.get("/ping")
        assert resp.status_code == 200
        assert resp.headers["X-Request-ID"]

    def test_propagates_valid_client_request_id(self) -> None:
        """A valid client-supplied X-Request-ID is echoed back verbatim."""
        client = _app_with_middleware()
        resp = client.get("/ping", headers={"X-Request-ID": "client-supplied-id-001"})
        assert resp.headers["X-Request-ID"] == "client-supplied-id-001"

    def test_rejects_invalid_client_request_id(self) -> None:
        """A malformed X-Request-ID is not trusted verbatim; a fresh one is used."""
        client = _app_with_middleware()
        resp = client.get("/ping", headers={"X-Request-ID": "short"})
        assert resp.headers["X-Request-ID"] != "short"
        assert resp.headers["X-Request-ID"]

    def test_binds_correlation_id_to_structlog_contextvars(self) -> None:
        """The request ID should be bound as structlog's correlation_id.

        This is what makes every log line emitted during the request share
        the same correlation_id as the X-Request-ID header, instead of
        add_correlation_id() minting an unrelated UUID per log call.
        """
        client = _app_with_middleware()
        resp = client.get("/ping", headers={"X-Request-ID": "client-supplied-id-002"})
        body = resp.json()
        assert body["correlation_id"] == "client-supplied-id-002"
        assert resp.headers["X-Request-ID"] == "client-supplied-id-002"

    def test_unbinds_correlation_id_after_request(self) -> None:
        """The correlation_id must not leak into logging done after the request."""
        client = _app_with_middleware()
        client.get("/ping", headers={"X-Request-ID": "client-supplied-id-003"})
        assert "correlation_id" not in structlog.contextvars.get_contextvars()


class TestCorrelationIdMatchesAcrossLogCalls:
    """End-to-end: two log calls within one bound scope share correlation_id."""

    def test_two_log_calls_share_correlation_id(self) -> None:
        """Binding correlation_id once should make every subsequent event_dict match."""
        structlog.contextvars.clear_contextvars()
        try:
            structlog.contextvars.bind_contextvars(correlation_id="shared-request-id")

            first: dict = {}
            second: dict = {}
            first = structlog.contextvars.merge_contextvars(None, "info", first)
            first = add_correlation_id(None, "info", first)  # type: ignore[arg-type]
            second = structlog.contextvars.merge_contextvars(None, "info", second)
            second = add_correlation_id(None, "info", second)  # type: ignore[arg-type]

            assert first["correlation_id"] == "shared-request-id"
            assert second["correlation_id"] == "shared-request-id"
        finally:
            structlog.contextvars.unbind_contextvars("correlation_id")
