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

import json
from unittest.mock import AsyncMock
from unittest.mock import patch

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

    def test_anonymous_requests_scoped_by_client_address(
        self,
        app_with_middleware: TestClient,
        mock_service: IdempotencyService,
    ) -> None:
        """Anonymous callers are scoped by client address, not one shared bucket.

        Previously every unauthenticated caller collapsed into the literal
        scope "anon", so two different anonymous clients presenting the same
        idempotency key (a plausible collision on a login/register endpoint,
        the key format permits something as guessable as "00000000") would
        replay each other's cached response, including any tokens it
        contained.
        """
        app_with_middleware.post(
            "/echo",
            json={"x": 1},
            headers={"X-Idempotency-Key": "test-key-anon"},
        )

        called_key = mock_service.get.await_args.args[0]

        assert called_key != "anon:POST:/echo:test-key-anon"
        assert called_key == "anon:testclient:POST:/echo:test-key-anon"

    def test_api_key_request_not_scoped_as_anonymous(
        self,
        app_with_middleware: TestClient,
        mock_service: IdempotencyService,
    ) -> None:
        """A caller authenticated via X-API-Key is not lumped into the anon bucket."""
        app_with_middleware.post(
            "/echo",
            json={"x": 1},
            headers={
                "X-Idempotency-Key": "test-key-apikey",
                "X-API-Key": "sk_test_abcdefgh",
            },
        )

        called_key = mock_service.get.await_args.args[0]

        assert not called_key.startswith("anon:")

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


# Service-level tests with mocked Redis (unit tests, no live Redis needed)


class TestCachedResponseFormat:
    """Tests for CachedResponse serialization format."""

    def test_serialized_output_is_valid_json_bytes(self) -> None:
        """_serialize returns valid JSON bytes with the expected keys."""
        svc = IdempotencyService()
        resp = CachedResponse(
            b'{"ok":true}',
            201,
            "application/json",
            headers={"X-Custom": "val"},
        )
        raw = svc._serialize(resp)

        assert isinstance(raw, bytes)
        data = json.loads(raw)
        assert "b" in data
        assert "s" in data
        assert "m" in data
        assert "h" in data
        assert data["s"] == 201
        assert data["m"] == "application/json"
        assert data["h"] == {"X-Custom": "val"}


class TestIdempotencyServiceWithMock:
    """Tests for IdempotencyService async methods with mocked Redis."""

    @pytest.mark.anyio
    async def test_cache_hit_returns_cached_response(self) -> None:
        """get returns deserialized CachedResponse when cache hit."""
        svc = IdempotencyService()
        original = CachedResponse(b'{"x":1}', 201, "application/json")
        serialized = svc._serialize(original)

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=serialized)

        with patch(
            "app.core.idempotency.get_redis", AsyncMock(return_value=mock_redis)
        ):
            result = await svc.get("test-key")

        assert result == original

    @pytest.mark.anyio
    async def test_cache_miss_returns_none(self) -> None:
        """get returns None when no cached data exists."""
        svc = IdempotencyService()

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        with patch(
            "app.core.idempotency.get_redis", AsyncMock(return_value=mock_redis)
        ):
            result = await svc.get("test-key")

        assert result is None

    @pytest.mark.anyio
    async def test_try_lock_acquires_with_ttl(self) -> None:
        """try_lock returns True and sets lock with TTL when available."""
        svc = IdempotencyService()

        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)

        with patch(
            "app.core.idempotency.get_redis", AsyncMock(return_value=mock_redis)
        ):
            result = await svc.try_lock("test-key")

        assert result is True
        mock_redis.set.assert_awaited_once_with(
            "idempotency:lock:test-key", "1", nx=True, ex=60
        )

    @pytest.mark.anyio
    async def test_try_lock_fails_on_conflict(self) -> None:
        """try_lock returns a falsy value when lock is already held."""
        svc = IdempotencyService()

        mock_redis = AsyncMock()
        # Redis returns None when SET NX fails (key already exists).
        mock_redis.set = AsyncMock(return_value=None)

        with patch(
            "app.core.idempotency.get_redis", AsyncMock(return_value=mock_redis)
        ):
            result = await svc.try_lock("test-key")

        assert not result

    @pytest.mark.anyio
    async def test_release_lock_deletes_lock_key(self) -> None:
        """release_lock deletes the lock key from Redis."""
        svc = IdempotencyService()

        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock()

        with patch(
            "app.core.idempotency.get_redis", AsyncMock(return_value=mock_redis)
        ):
            await svc.release_lock("test-key")

        mock_redis.delete.assert_awaited_once_with("idempotency:lock:test-key")

    @pytest.mark.anyio
    async def test_set_stores_serialized_response_with_ttl(self) -> None:
        """set stores serialized CachedResponse with the configured TTL."""
        svc = IdempotencyService()
        resp = CachedResponse(b'{"ok":true}', 200, "application/json")

        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()

        with patch(
            "app.core.idempotency.get_redis", AsyncMock(return_value=mock_redis)
        ):
            await svc.set("test-key", resp)

        mock_redis.setex.assert_awaited_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][0] == "idempotency:test-key"
        # Default IDEMPOTENCY_TTL_HOURS is 24 → 86400 seconds.
        assert call_args[0][1] == 86400

    @pytest.mark.anyio
    async def test_lock_releases_after_context_exit(self) -> None:
        """release_lock is called when using try_lock/release_lock as a context."""
        from contextlib import asynccontextmanager

        svc = IdempotencyService()

        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()

        cm_entered = False

        @asynccontextmanager
        async def idempotency_lock(key: str):
            nonlocal cm_entered
            acquired = await svc.try_lock(key)
            assert acquired is True
            cm_entered = True
            try:
                yield
            finally:
                await svc.release_lock(key)

        with patch(
            "app.core.idempotency.get_redis", AsyncMock(return_value=mock_redis)
        ):
            async with idempotency_lock("test-key"):
                assert cm_entered is True
                mock_redis.delete.assert_not_awaited()

        # After context exit, release_lock should have been called.
        mock_redis.delete.assert_awaited_once_with("idempotency:lock:test-key")

    @pytest.mark.anyio
    async def test_get_returns_none_for_expired_key(self) -> None:
        """get returns None when the cached key has expired (Redis returns None)."""
        svc = IdempotencyService()

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)  # Key expired / not found

        with patch(
            "app.core.idempotency.get_redis", AsyncMock(return_value=mock_redis)
        ):
            result = await svc.get("expired-key")

        assert result is None


class TestCachedResponse:
    """Tests for the CachedResponse dataclass."""

    def test_cached_response_with_custom_headers(self) -> None:
        """CachedResponse stores custom headers."""
        resp = CachedResponse(
            body=b'{"ok":true}',
            status_code=200,
            media_type="application/json",
            headers={"X-Custom": "value", "X-Another": "data"},
        )
        assert resp.body == b'{"ok":true}'
        assert resp.status_code == 200
        assert resp.media_type == "application/json"
        assert resp.headers == {"X-Custom": "value", "X-Another": "data"}

    def test_cached_response_with_custom_status_code(self) -> None:
        """CachedResponse accepts any HTTP status code."""
        resp = CachedResponse(
            body=b"",
            status_code=201,
            media_type=None,
        )
        assert resp.status_code == 201
        assert resp.media_type is None
        assert resp.headers is None

    def test_cached_response_defaults(self) -> None:
        """CachedResponse defaults headers to None."""
        resp = CachedResponse(body=b"data", status_code=204, media_type=None)
        assert resp.headers is None
        assert resp.media_type is None
        assert resp.body == b"data"
        assert resp.status_code == 204
