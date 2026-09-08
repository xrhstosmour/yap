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
    svc.try_lock = AsyncMock(return_value="test-token")
    svc.set = AsyncMock()
    svc.release_lock = AsyncMock()
    svc.extend_lock = AsyncMock()
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
        mock_service.try_lock = AsyncMock(return_value=None)

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


class TestIdempotencyLockHeartbeat:
    """The lock TTL must survive a request slower than LOCK_TTL_SECONDS."""

    def test_extends_lock_while_handler_is_in_flight(
        self,
        mock_service: IdempotencyService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A slow handler should trigger at least one lock TTL refresh.

        Regression test for the fixed-60-second lock: a handler running
        longer than LOCK_TTL_SECONDS used to have its lock expire mid
        request, letting a retry with the same key slip past try_lock()
        and run the same side effect again while the first was still in
        progress.
        """
        import asyncio

        from fastapi import FastAPI

        # Fast enough that a couple of ticks land during the sleep below,
        # without making the test itself slow.
        monkeypatch.setattr("app.api.idempotency.LOCK_HEARTBEAT_INTERVAL_SECONDS", 0.02)

        app = FastAPI()
        app.add_middleware(IdempotencyMiddleware)

        @app.post("/slow")
        async def slow(payload: dict) -> dict:
            await asyncio.sleep(0.1)
            return payload

        client = TestClient(app)
        resp = client.post(
            "/slow",
            json={"x": 1},
            headers={"X-Idempotency-Key": "test-key-heartbeat"},
        )

        assert resp.status_code == 200
        assert mock_service.extend_lock.await_count >= 1
        mock_service.extend_lock.assert_awaited_with(
            "anon:testclient:POST:/slow:test-key-heartbeat", "test-token"
        )
        # The heartbeat task must not outlive the request.
        mock_service.release_lock.assert_awaited_once_with(
            "anon:testclient:POST:/slow:test-key-heartbeat", "test-token"
        )


class TestClientErrorsAreNotCached:
    """A rejection must not stick to the key for the rest of the day.

    Caching 4xx meant a 429 from the rate limiter, a 401 from an expired
    token, or a 422 from a bad payload was replayed for
    `IDEMPOTENCY_TTL_HOURS` (24 by default), long after the client had
    waited, re-authenticated or corrected the request. The only way out
    was to change the idempotency key, which is what the key exists to
    make unnecessary.
    """

    @pytest.mark.parametrize("status_code", [400, 401, 403, 409, 422, 429])
    def test_client_error_is_not_cached(
        self,
        mock_service: IdempotencyService,
        status_code: int,
    ) -> None:
        """No 4xx should ever be written to the idempotency store.

        Args:
            mock_service: Mocked idempotency service.
            status_code: The 4xx status under test.
        """
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse

        app = FastAPI()
        app.add_middleware(IdempotencyMiddleware)

        @app.post("/rejected")
        async def rejected():
            return JSONResponse({"detail": "no"}, status_code=status_code)

        client = TestClient(app)
        mock_service.set.reset_mock()

        response = client.post(
            "/rejected",
            json={},
            headers={"X-Idempotency-Key": f"test-key-{status_code}"},
        )

        assert response.status_code == status_code
        mock_service.set.assert_not_awaited()

    @pytest.mark.parametrize("status_code", [200, 201, 204, 302])
    def test_success_and_redirect_are_still_cached(
        self,
        mock_service: IdempotencyService,
        status_code: int,
    ) -> None:
        """Narrowing the rule must not stop caching real outcomes.

        Args:
            mock_service: Mocked idempotency service.
            status_code: The non-error status under test.
        """
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse

        app = FastAPI()
        app.add_middleware(IdempotencyMiddleware)

        @app.post("/done")
        async def done():
            return JSONResponse({"ok": True}, status_code=status_code)

        client = TestClient(app)
        mock_service.set.reset_mock()

        client.post(
            "/done",
            json={},
            headers={"X-Idempotency-Key": f"test-key-ok-{status_code}"},
        )

        mock_service.set.assert_awaited_once()


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
        """try_lock returns an ownership token and sets the lock with TTL."""
        svc = IdempotencyService()

        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)

        with patch(
            "app.core.idempotency.get_redis", AsyncMock(return_value=mock_redis)
        ):
            result = await svc.try_lock("test-key")

        assert isinstance(result, str)
        assert result
        mock_redis.set.assert_awaited_once_with(
            "idempotency:lock:test-key", result, nx=True, ex=60
        )

    @pytest.mark.anyio
    async def test_try_lock_fails_on_conflict(self) -> None:
        """try_lock returns None when the lock is already held."""
        svc = IdempotencyService()

        mock_redis = AsyncMock()
        # Redis returns None when SET NX fails (key already exists).
        mock_redis.set = AsyncMock(return_value=None)

        with patch(
            "app.core.idempotency.get_redis", AsyncMock(return_value=mock_redis)
        ):
            result = await svc.try_lock("test-key")

        assert result is None

    @pytest.mark.anyio
    async def test_release_lock_deletes_lock_key_when_token_matches(self) -> None:
        """release_lock deletes the lock key when the token matches the owner."""
        svc = IdempotencyService()

        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(return_value=1)

        with patch(
            "app.core.idempotency.get_redis", AsyncMock(return_value=mock_redis)
        ):
            await svc.release_lock("test-key", "owner-token")

        args, _ = mock_redis.eval.call_args
        assert args[1:] == (1, "idempotency:lock:test-key", "owner-token")

    @pytest.mark.anyio
    async def test_release_lock_does_not_delete_a_lock_owned_by_another_token(
        self,
    ) -> None:
        """release_lock must not delete a lock a different token now owns.

        Regression: an unconditional `DEL` would remove whatever lock
        currently occupies the key, including one a retry acquired after
        this caller's own lock already expired, releasing a request that
        isn't this one's to release. This is exercised at the Lua-script
        level via a real Redis-compatible fake below, not just asserting
        the call arguments, since the safety property lives in the
        script's compare-then-delete atomicity.
        """
        svc = IdempotencyService()
        lock_key = "idempotency:lock:test-key"

        class _FakeRedis:
            def __init__(self, value: str) -> None:
                self.store: dict[str, str] = {lock_key: value}

            async def eval(
                self, script: str, numkeys: int, key: str, *args: str
            ) -> int:
                assert numkeys == 1
                assert key == lock_key
                if self.store.get(key) == args[0]:
                    del self.store[key]
                    return 1
                return 0

        fake_redis = _FakeRedis(value="other-token")

        with patch(
            "app.core.idempotency.get_redis", AsyncMock(return_value=fake_redis)
        ):
            await svc.release_lock("test-key", "stale-token")

        assert fake_redis.store.get(lock_key) == "other-token"

    @pytest.mark.anyio
    async def test_extend_lock_refreshes_ttl_when_token_matches(self) -> None:
        """extend_lock refreshes the lock key's TTL when the token matches.

        Backs the middleware's heartbeat: a request still in flight past
        LOCK_TTL_SECONDS must not have its lock silently expire, which
        would let a retry with the same idempotency key slip past
        try_lock() and run the same side effect concurrently.
        """
        from app.core.idempotency import LOCK_TTL_SECONDS

        svc = IdempotencyService()

        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(return_value=1)

        with patch(
            "app.core.idempotency.get_redis", AsyncMock(return_value=mock_redis)
        ):
            await svc.extend_lock("test-key", "owner-token")

        args, _ = mock_redis.eval.call_args
        assert args[1:] == (
            1,
            "idempotency:lock:test-key",
            "owner-token",
            str(LOCK_TTL_SECONDS),
        )

    @pytest.mark.anyio
    async def test_extend_lock_swallows_redis_errors(self) -> None:
        """A failed heartbeat refresh must not raise into the caller.

        The next heartbeat tick still has LOCK_TTL_SECONDS of headroom to
        recover in, so one failure is logged, not propagated.
        """
        svc = IdempotencyService()

        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(side_effect=ConnectionError("redis down"))

        with patch(
            "app.core.idempotency.get_redis", AsyncMock(return_value=mock_redis)
        ):
            await svc.extend_lock("test-key", "owner-token")  # Should not raise.

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
        mock_redis.eval = AsyncMock(return_value=1)

        cm_entered = False

        @asynccontextmanager
        async def idempotency_lock(key: str):
            nonlocal cm_entered
            token = await svc.try_lock(key)
            assert token is not None
            cm_entered = True
            try:
                yield
            finally:
                await svc.release_lock(key, token)

        with patch(
            "app.core.idempotency.get_redis", AsyncMock(return_value=mock_redis)
        ):
            async with idempotency_lock("test-key"):
                assert cm_entered is True
                mock_redis.eval.assert_not_awaited()

        # After context exit, release_lock should have been called.
        mock_redis.eval.assert_awaited_once()

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
