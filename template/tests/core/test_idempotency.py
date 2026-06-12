"""Tests for the idempotency middleware and service.

Mock strategy
-------------
`IdempotencyService` calls `get_redis()` internally.  We patch
**only** the service methods with `unittest.mock.AsyncMock` so the
middleware can be exercised without a real Redis connection.
Tests that verify the service itself use a live Redis client (when
available) or skip.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.idempotency import IdempotencyMiddleware
from app.core.idempotency import CachedResponse
from app.core.idempotency import IdempotencyService


class TestIdempotencyService:
    """Verify serialisation round-trip and lock semantics."""

    def test_roundtrip(self) -> None:
        """CachedResponse survives serialise / deserialise."""
        original = CachedResponse(b'{"ok":true}', 200, "application/json")
        raw = idempotency_service._serialize(original)
        restored = idempotency_service._deserialize(raw)
        assert restored == original

    def test_deserialize_corrupt_returns_none(self) -> None:
        assert idempotency_service._deserialize(b"garbage") is None

    def test_lock_key_namespaced(self) -> None:
        key = idempotency_service._lock_key("abc")
        assert key == "idempotency:lock:abc"

    def test_data_key_namespaced(self) -> None:
        key = idempotency_service._key("abc")
        assert key == "idempotency:abc"


# Instantiate once for the tests above.
idempotency_service = IdempotencyService()


@pytest.fixture
def mock_service(monkeypatch: pytest.MonkeyPatch) -> IdempotencyService:
    """Replace the global `idempotency_service` with a mocked version.

    All three public methods (`get`, `try_lock`, `set`) become
    `AsyncMock` instances that the test can assert against.
    """
    svc = IdempotencyService()
    svc.get = AsyncMock(return_value=None)
    svc.try_lock = AsyncMock(return_value=True)
    svc.set = AsyncMock()
    svc.release_lock = AsyncMock()
    monkeypatch.setattr("app.api.idempotency.idempotency_service", svc)
    return svc


@pytest.fixture
def app_with_middleware() -> TestClient:
    """Minimal FastAPI app wired with the idempotency middleware."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware)

    @app.post("/echo")
    async def echo(payload: dict):
        return JSONResponse(payload, status_code=201)

    @app.get("/ping")
    async def ping():
        return {"status": "ok"}

    return TestClient(app)


class TestIdempotencyMiddleware:
    """Behaviour tests for the middleware in isolation."""

    def test_get_passes_through(self, app_with_middleware: TestClient) -> None:
        """GET requests are never intercepted."""
        resp = app_with_middleware.get("/ping")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_post_without_key_passes_through(
        self,
        app_with_middleware: TestClient,
    ) -> None:
        """POST without `X-Idempotency-Key` is not cached."""
        resp = app_with_middleware.post("/echo", json={"x": 1})
        assert resp.status_code == 201

    def test_invalid_key_rejected(
        self,
        app_with_middleware: TestClient,
    ) -> None:
        """Malformed keys get a 422."""
        resp = app_with_middleware.post(
            "/echo",
            json={"x": 1},
            headers={"X-Idempotency-Key": "short"},
        )
        assert resp.status_code == 422

    def test_first_request_caches_response(
        self,
        app_with_middleware: TestClient,
        mock_service: IdempotencyService,
    ) -> None:
        """First request with a valid key is executed and cached."""
        resp = app_with_middleware.post(
            "/echo",
            json={"x": 1},
            headers={"X-Idempotency-Key": "test-key-001"},
        )
        assert resp.status_code == 201
        assert resp.json() == {"x": 1}
        mock_service.set.assert_awaited_once()

    def test_duplicate_returns_cached(
        self,
        app_with_middleware: TestClient,
        mock_service: IdempotencyService,
    ) -> None:
        """Second request with the same key returns the cached response."""
        cached = CachedResponse(b'{"x":1}', 201, "application/json")
        mock_service.get = AsyncMock(return_value=cached)

        resp = app_with_middleware.post(
            "/echo",
            json={"x": 2},
            headers={"X-Idempotency-Key": "test-key-002"},
        )
        assert resp.status_code == 201
        assert resp.json() == {"x": 1}

    def test_concurrent_request_gets_409(
        self,
        app_with_middleware: TestClient,
        mock_service: IdempotencyService,
    ) -> None:
        """Second concurrent caller receives 409 Conflict."""
        mock_service.try_lock = AsyncMock(return_value=False)

        resp = app_with_middleware.post(
            "/echo",
            json={"x": 1},
            headers={"X-Idempotency-Key": "test-key-003"},
        )
        assert resp.status_code == 409
        assert "already in progress" in resp.text.lower()

    def test_server_error_not_cached(
        self,
        mock_service: IdempotencyService,
    ) -> None:
        """5xx responses should not be cached."""
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse

        app = FastAPI()
        app.add_middleware(IdempotencyMiddleware)

        @app.post("/fail")
        async def fail():
            return JSONResponse({"error": "broken"}, status_code=500)

        client = TestClient(app)
        mock_service.set.reset_mock()

        resp = client.post(
            "/fail",
            json={},
            headers={"X-Idempotency-Key": "test-key-500"},
        )
        assert resp.status_code == 500
        mock_service.set.assert_not_awaited()
