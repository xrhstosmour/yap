"""Tests for the global rate limit and the probing block."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any
from uuid import uuid4

import jwt
import pytest
import redis.asyncio as redis_asyncio
from fastapi import FastAPI
from fastapi import WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisClusterException
from redis.exceptions import RedisError

from app.api.global_rate_limit import COUNT_FAILURE_SCRIPT
from app.api.global_rate_limit import Caller
from app.api.global_rate_limit import GlobalRateLimitMiddleware
from app.api.global_rate_limit import _keys
from app.api.global_rate_limit import normalize_client_address
from app.core.security import _JWT_BLACKLIST_PREFIX
from app.core.security import ALGORITHM
from app.core.security import create_access_token
from app.core.security import create_refresh_token
from app.core.settings import settings

ORIGIN = "https://app.example.com"
ANONYMOUS_LIMIT = 3
AUTHENTICATED_LIMIT = 5
API_KEY_LIMIT = 4
FAILURE_THRESHOLD = 3


class FakeRedis:
    """Just enough Redis for the limiters: windows, a failure count, a block key.

    The failure script is run as the one step it is, so a test that needs it
    split into separate calls fails on a missing method. Like a Redis Cluster
    client it refuses a script whose keys do not share a hash tag.
    """

    def __init__(self) -> None:
        self.windows: dict[str, int] = {}
        self.counters: dict[str, int] = {}
        self.expiries: dict[str, int] = {}
        self.values: dict[str, str] = {}
        self.events: list[str] = []
        self.retry_afters: dict[str, int] = {}

    async def eval(self, script: str, _keys: int, key: str, *arguments: str) -> Any:  # noqa: ANN401
        if script == COUNT_FAILURE_SCRIPT:
            tags = {
                name[name.index("{") : name.index("}")] for name in (key, arguments[0])
            }
            if len(tags) != 1:
                raise RedisClusterException(
                    "eval - all keys must map to the same key slot"
                )
            return self._count_failure(key, *arguments)
        limit = int(arguments[2])
        count = self.windows.get(key, 0)
        if count < limit:
            self.windows[key] = count + 1
            return [0, limit - count - 1, 0]
        retry_after = next(
            (wait for part, wait in self.retry_afters.items() if part in key), 42
        )
        return [1, 0, retry_after]

    def _count_failure(
        self, key: str, block_key: str, window: str, threshold: str, block: str
    ) -> int:
        self.events.append("count")
        self.counters[key] = self.counters.get(key, 0) + 1
        self.expiries.setdefault(key, int(window))
        if self.counters[key] >= int(threshold):
            self.values[block_key] = "1"
            self.expiries[block_key] = int(block)
            return 1
        return 0

    async def ttl(self, key: str) -> int:
        return self.expiries.get(key, -2) if key in self.values else -2

    async def exists(self, key: str) -> int:
        return int(key in self.values)


class BrokenRedis:
    """Every call fails, as when Redis is down."""

    async def eval(self, *_arguments: object) -> None:
        raise RedisConnectionError("down")

    ttl = exists = eval


class ClusterBrokenRedis(BrokenRedis):
    """A cluster client that cannot place the command."""

    async def eval(self, *_arguments: object) -> None:
        raise RedisClusterException("no node")

    ttl = exists = eval


class HangingRedis(FakeRedis):
    """Counting a failure, or only the allowance, never finishes."""

    def __init__(self, *, hang_on_allowance: bool = False) -> None:
        super().__init__()
        self.hang_on_allowance = hang_on_allowance

    async def eval(self, script: str, *arguments: Any) -> Any:  # noqa: ANN401
        if (script == COUNT_FAILURE_SCRIPT) != self.hang_on_allowance:
            await asyncio.sleep(30)
        return await super().eval(script, *arguments)


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """Back both limiters with one in-memory Redis."""
    fake = FakeRedis()

    async def _get_redis() -> FakeRedis:
        return fake

    monkeypatch.setattr("app.api.global_rate_limit.get_redis", _get_redis)
    monkeypatch.setattr("app.core.rate_limit.get_redis", _get_redis)
    monkeypatch.setattr("app.core.cache.get_redis", _get_redis)
    return fake


@pytest.fixture(autouse=True)
def _enabled_with_small_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt back in, the shared fixture switches the limiter off."""
    monkeypatch.setattr(settings, "RATE_LIMIT_GLOBAL_ENABLED", True)
    monkeypatch.setattr(
        settings, "RATE_LIMIT_GLOBAL_ANONYMOUS_PER_MINUTE", ANONYMOUS_LIMIT
    )
    monkeypatch.setattr(
        settings, "RATE_LIMIT_GLOBAL_AUTHENTICATED_PER_MINUTE", AUTHENTICATED_LIMIT
    )
    monkeypatch.setattr(settings, "RATE_LIMIT_GLOBAL_API_KEY_PER_MINUTE", API_KEY_LIMIT)
    monkeypatch.setattr(settings, "ABUSE_FAILURE_THRESHOLD", FAILURE_THRESHOLD)
    monkeypatch.setattr(settings, "ABUSE_FAILURE_WINDOW_SECONDS", 60)
    monkeypatch.setattr(settings, "ABUSE_BLOCK_SECONDS", 300)


def _application() -> FastAPI:
    """A small app with the middleware inside CORS, as `main` builds it."""
    application = FastAPI()

    @application.get("/thing")
    async def thing() -> dict[str, bool]:
        return {"ok": True}

    @application.get("/number/{value}")
    async def number(value: int) -> dict[str, int]:
        return {"value": value}

    @application.get("/unauthorized", status_code=401)
    async def unauthorized() -> dict[str, bool]:
        return {"ok": False}

    @application.get("/forbidden", status_code=403)
    async def forbidden() -> dict[str, bool]:
        return {"ok": False}

    @application.get("/api/v1/ready")
    async def ready() -> dict[str, bool]:
        return {"ok": True}

    @application.get("/admin/page")
    async def admin_page() -> dict[str, bool]:
        return {"ok": True}

    @application.websocket("/ws")
    async def socket(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json({"ok": True})
        await websocket.close()

    application.add_middleware(GlobalRateLimitMiddleware)
    application.add_middleware(
        CORSMiddleware, allow_origins=[ORIGIN], allow_methods=["*"]
    )
    return application


def _client(address: str = "203.0.113.7") -> TestClient:
    return TestClient(_application(), client=(address, 50000))


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.usefixtures("redis")
class TestTiers:
    """Each caller is counted under its own key and allowance."""

    def test_anonymous_callers_are_limited_per_address(self) -> None:
        """The request after the allowance is a 429."""
        client = _client()

        statuses = [
            client.get("/thing").status_code for _ in range(ANONYMOUS_LIMIT + 1)
        ]

        assert statuses == [200] * ANONYMOUS_LIMIT + [429]

    def test_another_address_has_its_own_allowance(self) -> None:
        """One address being limited leaves the others alone."""
        for _ in range(ANONYMOUS_LIMIT + 1):
            _client("203.0.113.7").get("/thing")

        assert _client("203.0.113.8").get("/thing").status_code == 200

    def test_a_valid_token_gets_the_user_allowance(self) -> None:
        """Signed-in callers get the larger one, keyed by user not address."""
        client = _client()
        headers = _bearer(create_access_token(subject="user-1"))

        statuses = [
            client.get("/thing", headers=headers).status_code
            for _ in range(AUTHENTICATED_LIMIT + 1)
        ]

        assert statuses == [200] * AUTHENTICATED_LIMIT + [429]

    def test_the_user_allowance_follows_the_user_across_addresses(self) -> None:
        """A user is one caller wherever the request comes from."""
        headers = _bearer(create_access_token(subject="user-2"))
        for index in range(AUTHENTICATED_LIMIT):
            _client(f"198.51.100.{index}").get("/thing", headers=headers)

        response = _client("198.51.100.99").get("/thing", headers=headers)

        assert response.status_code == 429

    def test_user_traffic_does_not_use_up_the_address_allowance(self) -> None:
        """The two tiers count separately."""
        client = _client()
        headers = _bearer(create_access_token(subject="user-3"))
        for _ in range(ANONYMOUS_LIMIT):
            client.get("/thing", headers=headers)

        assert client.get("/thing").status_code == 200

    @pytest.mark.parametrize(
        "headers_factory",
        [
            lambda: _bearer(
                create_access_token(subject="u", expires_delta=timedelta(seconds=-5))
            ),
            lambda: _bearer(
                jwt.encode({"sub": "u", "type": "access"}, "x" * 40, ALGORITHM)
            ),
            lambda: _bearer(create_refresh_token(subject="u")),
            lambda: _bearer("not-a-token"),
            lambda: {"X-API-Key": "key_abc"},
            lambda: {"X-API-Key": "key_abc:"},
            lambda: {"X-API-Key": ":secret"},
        ],
        ids=[
            "expired",
            "forged",
            "refresh-token",
            "garbage",
            "key-without-separator",
            "key-without-secret",
            "secret-without-key",
        ],
    )
    def test_anything_else_falls_back_to_the_address_tier(
        self,
        headers_factory: Any,  # noqa: ANN401
    ) -> None:
        """No unverified credential earns the higher allowance."""
        client = _client()

        statuses = [
            client.get("/thing", headers=headers_factory()).status_code
            for _ in range(ANONYMOUS_LIMIT + 1)
        ]

        assert statuses == [200] * ANONYMOUS_LIMIT + [429]

    def test_an_api_key_has_its_own_allowance_across_addresses(self) -> None:
        """The same key is one caller wherever it is used from."""
        statuses = [
            _client(f"198.51.100.{index}")
            .get("/thing", headers={"X-API-Key": "key_abc:secret"})
            .status_code
            for index in range(API_KEY_LIMIT + 1)
        ]

        assert statuses == [200] * API_KEY_LIMIT + [429]

    def test_a_forged_secret_does_not_spend_the_real_keys_allowance(self) -> None:
        """Knowing a key ID is not enough to land in its bucket."""
        for index in range(API_KEY_LIMIT + 2):
            _client("198.51.100.50").get(
                "/thing", headers={"X-API-Key": f"key_abc:forged-{index}"}
            )

        real = _client().get("/thing", headers={"X-API-Key": "key_abc:secret"})

        assert real.status_code == 200

    def test_rotating_api_keys_still_hit_the_address_allowance(self) -> None:
        """A fresh made-up key per request is not a fresh allowance."""
        client = _client()

        statuses = [
            client.get("/thing", headers={"X-API-Key": f"a:{index}"}).status_code
            for index in range(ANONYMOUS_LIMIT + 1)
        ]

        assert statuses == [200] * ANONYMOUS_LIMIT + [429]

    def test_the_longest_wait_among_refusing_allowances_is_reported(
        self, redis: FakeRedis
    ) -> None:
        """Coming back after the key frees up would still meet the address limit."""
        redis.retry_afters = {"ratelimit:global:key": 5, "ratelimit:global:ip": 40}
        client = _client()
        headers = {"X-API-Key": "key_abc:secret"}
        for _ in range(ANONYMOUS_LIMIT):
            client.get("/thing", headers=headers)
        # The key allowance is spent too, from other addresses.
        for index in range(API_KEY_LIMIT):
            _client(f"198.51.100.{index}").get("/thing", headers=headers)

        response = client.get("/thing", headers=headers)

        assert response.status_code == 429
        assert int(response.headers["retry-after"]) >= 40

    def test_a_revoked_token_with_an_api_key_falls_back_to_the_address(
        self, redis: FakeRedis
    ) -> None:
        """A logged-out token does not earn the user bucket next to a key."""
        token = create_access_token(subject="user-8")
        identifier = jwt.decode(token, options={"verify_signature": False})["jti"]
        redis.values[f"{_JWT_BLACKLIST_PREFIX}:{identifier}"] = "1"
        client = _client()
        headers = {**_bearer(token), "X-API-Key": "key_abc:secret"}

        statuses = [
            client.get("/thing", headers=headers).status_code
            for _ in range(ANONYMOUS_LIMIT + 1)
        ]

        assert statuses == [200] * ANONYMOUS_LIMIT + [429]
        assert not any("user-8" in key for key in redis.windows)

    def test_an_api_key_adds_to_the_address_count_not_replaces_it(self) -> None:
        """Key requests spend the address allowance too, other keys do not share."""
        for _ in range(ANONYMOUS_LIMIT):
            _client().get("/thing", headers={"X-API-Key": "key_abc:secret"})

        assert _client().get("/thing").status_code == 429
        other_key = _client("198.51.100.4").get(
            "/thing", headers={"X-API-Key": "key_def:secret"}
        )
        assert other_key.status_code == 200
        assert (
            _client("198.51.100.5")
            .get("/thing", headers={"X-API-Key": "key_abc:secret"})
            .status_code
            == 200
        )

    def test_clients_in_one_ipv6_block_share_the_allowance(self) -> None:
        """Changing the low 64 bits does not give a fresh allowance."""
        for index in range(ANONYMOUS_LIMIT):
            _client(f"2001:db8:0:1::{index + 1}").get("/thing")

        assert _client("2001:db8:0:1:aaaa::9").get("/thing").status_code == 429
        assert _client("2001:db8:0:2::1").get("/thing").status_code == 200

    def test_mapped_ipv4_shares_the_allowance_of_the_plain_address(self) -> None:
        """`::ffff:a.b.c.d` is the same client as `a.b.c.d`."""
        for _ in range(ANONYMOUS_LIMIT):
            _client("::ffff:203.0.113.9").get("/thing")

        assert _client("203.0.113.9").get("/thing").status_code == 429


class TestKeys:
    """The failure count and block keys live in one slot of a Redis Cluster."""

    @pytest.mark.parametrize(
        "caller",
        [
            Caller("ip", "203.0.113.7"),
            Caller("ip", "2001:db8:0:1::/64"),
            Caller("user", "0198f8a0-0000-7000-8000-000000000001"),
            Caller("key", "a" * 32),
        ],
    )
    def test_both_keys_share_one_hash_tag(self, caller: Caller) -> None:
        """Redis hashes only the first `{...}`, so it must be the same one."""
        failures, block = _keys(caller)

        assert failures == f"abuse:{{{caller.scope}:{caller.identifier}}}:failures"
        assert block == f"abuse:{{{caller.scope}:{caller.identifier}}}:block"


class TestNormalizeClientAddress:
    """Addresses are written one way before they are counted."""

    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("::ffff:203.0.113.9", "203.0.113.9"),
            ("203.0.113.9", "203.0.113.9"),
            ("2001:DB8::1", "2001:db8::/64"),
            ("2001:db8:0:1:ffff:ffff:ffff:ffff", "2001:db8:0:1::/64"),
            ("2001:db8:0:2::1", "2001:db8:0:2::/64"),
            ("fe80::1%eth0", "fe80::/64"),
            ("testclient", "testclient"),
            (None, "unknown"),
        ],
    )
    def test_normalize(self, host: str | None, expected: str) -> None:
        """Mapped addresses become IPv4, IPv6 its /64, the rest is kept."""
        assert normalize_client_address(host) == expected


@pytest.mark.usefixtures("redis")
class TestExemptions:
    """Probes, preflights, WebSockets and the admin surface are never limited."""

    @pytest.mark.parametrize("path", ["/api/v1/ready", "/admin/page"])
    def test_exempt_paths_pass_past_the_allowance(self, path: str) -> None:
        """Past the allowance they still answer."""
        client = _client()
        for _ in range(ANONYMOUS_LIMIT):
            client.get("/thing")

        assert client.get(path).status_code == 200
        assert client.get("/thing").status_code == 429

    def test_preflight_requests_pass(self) -> None:
        """OPTIONS is not counted or refused."""
        client = _client()
        for _ in range(ANONYMOUS_LIMIT):
            client.get("/thing")

        response = client.options(
            "/thing",
            headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET"},
        )

        assert response.status_code == 200

    def test_websockets_pass(self) -> None:
        """The HTTP limiter leaves the WebSocket scope alone."""
        client = _client()
        for _ in range(ANONYMOUS_LIMIT):
            client.get("/thing")

        with client.websocket_connect("/ws") as websocket:
            assert websocket.receive_json() == {"ok": True}

    def test_disabled_limiter_passes_everything(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`RATE_LIMIT_GLOBAL_ENABLED` switches it off."""
        monkeypatch.setattr(settings, "RATE_LIMIT_GLOBAL_ENABLED", False)
        client = _client()

        statuses = {
            client.get("/thing").status_code for _ in range(ANONYMOUS_LIMIT * 3)
        }

        assert statuses == {200}


@pytest.mark.usefixtures("redis")
class TestRefusal:
    """A refusal says when to come back and is a proper response."""

    def test_the_429_carries_retry_after_and_cors_headers(self) -> None:
        """The browser must be able to read the refusal."""
        client = _client()
        for _ in range(ANONYMOUS_LIMIT):
            client.get("/thing")

        response = client.get("/thing", headers={"Origin": ORIGIN})

        assert response.status_code == 429
        assert response.headers["retry-after"] == "43"
        assert response.headers["access-control-allow-origin"] == ORIGIN
        assert response.json() == {"detail": "Rate limit exceeded"}


class TestFailureBlock:
    """A caller that keeps hitting 404, 405 and 422 is shut out for a while."""

    def test_the_threshold_blocks_the_address(self, redis: FakeRedis) -> None:
        """After enough probes every request is a 429 with the time left."""
        client = _client()
        for _ in range(FAILURE_THRESHOLD):
            assert client.get("/missing").status_code == 404

        response = client.get("/thing")

        assert response.status_code == 429
        assert response.headers["retry-after"] == "300"
        assert redis.expiries["abuse:{ip:203.0.113.7}:block"] == 300

    @pytest.mark.parametrize(
        ("method", "path"),
        [("get", "/missing"), ("post", "/thing"), ("get", "/number/not-a-number")],
        ids=["404", "405", "422"],
    )
    def test_each_probing_status_counts(
        self, redis: FakeRedis, method: str, path: str
    ) -> None:
        """404, 405 and 422 all add to the count."""
        client = _client()

        getattr(client, method)(path)

        assert redis.counters == {"abuse:{ip:203.0.113.7}:failures": 1}

    @pytest.mark.parametrize("path", ["/unauthorized", "/forbidden"])
    def test_401_and_403_are_not_counted(self, redis: FakeRedis, path: str) -> None:
        """Honest clients meet these, they never lead to a block."""
        client = _client()
        for _ in range(FAILURE_THRESHOLD + 2):
            client.get(path)

        assert redis.counters == {}
        assert "abuse:{ip:203.0.113.7}:block" not in redis.values

    def test_a_refused_request_is_not_counted(self, redis: FakeRedis) -> None:
        """A 429 does not feed the counter that caused it."""
        client = _client()
        for _ in range(ANONYMOUS_LIMIT + 3):
            client.get("/thing")

        assert redis.counters == {}

    def test_a_signed_in_user_is_blocked_by_user_not_address(
        self, redis: FakeRedis
    ) -> None:
        """Probing under a token blocks that user, wherever they connect from."""
        headers = _bearer(create_access_token(subject="user-4"))
        for _ in range(FAILURE_THRESHOLD):
            _client().get("/missing", headers=headers)

        assert redis.counters == {"abuse:{user:user-4}:failures": FAILURE_THRESHOLD}
        blocked = _client("198.51.100.20").get("/thing", headers=headers)
        assert blocked.status_code == 429
        assert blocked.headers["retry-after"] == "300"
        other_user = _bearer(create_access_token(subject="user-6"))
        assert _client().get("/thing", headers=other_user).status_code == 200
        assert _client().get("/thing").status_code == 200

    def test_a_signed_in_user_on_a_blocked_address_still_passes(
        self, redis: FakeRedis
    ) -> None:
        """The address block is for traffic counted by address, not for users."""
        client = _client()
        for _ in range(FAILURE_THRESHOLD):
            client.get("/missing")
        assert client.get("/thing").status_code == 429

        headers = _bearer(create_access_token(subject="user-5"))

        assert client.get("/thing", headers=headers).status_code == 200

    def test_an_api_key_is_blocked_by_a_hash_of_its_value(
        self, redis: FakeRedis
    ) -> None:
        """Probing with a key blocks that key, and the value is not stored."""
        for _ in range(FAILURE_THRESHOLD):
            _client().get("/missing", headers={"X-API-Key": "key_abc:secret"})

        refused = _client("198.51.100.30").get(
            "/thing", headers={"X-API-Key": "key_abc:secret"}
        )

        assert refused.status_code == 429
        assert all("key_abc" not in key for key in redis.counters)
        other = _client("198.51.100.31").get(
            "/thing", headers={"X-API-Key": "key_def:secret"}
        )
        assert other.status_code == 200

    def test_rotating_api_keys_cannot_reset_the_address_block(
        self, redis: FakeRedis
    ) -> None:
        """Probing under a new made-up key each time still blocks the address."""
        client = _client()
        for index in range(FAILURE_THRESHOLD):
            assert (
                client.get("/missing", headers={"X-API-Key": f"a:{index}"}).status_code
                == 404
            )

        refused = client.get("/thing", headers={"X-API-Key": "a:fresh"})
        assert refused.status_code == 429
        assert refused.headers["retry-after"] == "300"

    def test_a_forged_secret_cannot_block_the_real_key(self, redis: FakeRedis) -> None:
        """Failures under a made-up secret block that secret and its address."""
        for _ in range(FAILURE_THRESHOLD):
            _client().get("/missing", headers={"X-API-Key": "key_abc:forged"})

        forged = _client().get("/thing", headers={"X-API-Key": "key_abc:forged"})
        real = _client("198.51.100.32").get(
            "/thing", headers={"X-API-Key": "key_abc:secret"}
        )

        assert forged.status_code == 429
        assert real.status_code == 200

    def test_a_revoked_token_falls_back_to_the_address(self, redis: FakeRedis) -> None:
        """A logged-out token cannot spend or block its user's bucket."""
        token = create_access_token(subject="user-7")
        identifier = jwt.decode(token, options={"verify_signature": False})["jti"]
        redis.values[f"{_JWT_BLACKLIST_PREFIX}:{identifier}"] = "1"
        client = _client()

        statuses = [
            client.get("/thing", headers=_bearer(token)).status_code
            for _ in range(ANONYMOUS_LIMIT + 1)
        ]

        assert statuses == [200] * ANONYMOUS_LIMIT + [429]
        assert not any("user-7" in key for key in redis.windows)
        for _ in range(FAILURE_THRESHOLD):
            _client("198.51.100.40").get("/missing", headers=_bearer(token))
        assert not any("user-7" in key for key in redis.counters)

    def test_a_failure_is_counted_after_the_response_is_sent(
        self, redis: FakeRedis
    ) -> None:
        """The last byte goes out before Redis is asked to count."""
        sent: list[str] = []

        async def application(_scope: Any, _receive: Any, send: Any) -> None:  # noqa: ANN401
            await send({"type": "http.response.start", "status": 404, "headers": []})
            await send({"type": "http.response.body", "body": b"nope"})

        async def record(message: Any) -> None:  # noqa: ANN401
            sent.append(message["type"])
            redis.events.append(message["type"])

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/missing",
            "headers": [],
            "client": ("203.0.113.7", 50000),
        }

        async def run() -> None:
            await GlobalRateLimitMiddleware(application)(scope, _no_receive, record)

        asyncio.run(run())

        assert redis.events == [
            "http.response.start",
            "http.response.body",
            "count",
        ]

    def test_counting_that_hangs_does_not_hold_the_request_for_long(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stuck Redis is given up on after the timeout, nothing is raised."""
        _use_redis(monkeypatch, HangingRedis())
        monkeypatch.setattr("app.api.global_rate_limit.REDIS_TIMEOUT_SECONDS", 0.05)

        started = time.monotonic()
        response = _client().get("/missing")

        assert response.status_code == 404
        assert time.monotonic() - started < 2

    def test_an_allowance_check_that_hangs_fails_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No answer in time means the request goes through, quickly."""
        _use_redis(monkeypatch, HangingRedis(hang_on_allowance=True))
        monkeypatch.setattr("app.api.global_rate_limit.REDIS_TIMEOUT_SECONDS", 0.05)

        started = time.monotonic()
        response = _client().get("/thing")

        assert response.status_code == 200
        assert time.monotonic() - started < 2


def _use_redis(monkeypatch: pytest.MonkeyPatch, fake: Any) -> None:  # noqa: ANN401
    """Make every module under test talk to `fake`."""

    async def _get_redis() -> Any:  # noqa: ANN401
        return fake

    monkeypatch.setattr("app.api.global_rate_limit.get_redis", _get_redis)
    monkeypatch.setattr("app.core.rate_limit.get_redis", _get_redis)
    monkeypatch.setattr("app.core.cache.get_redis", _get_redis)


async def _no_receive() -> dict[str, str]:
    return {"type": "http.request"}


class TestFailOpen:
    """Redis being down must not take the API down with it."""

    def test_requests_pass_when_redis_is_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both the allowance and the block are skipped."""

        async def _get_redis() -> BrokenRedis:
            return BrokenRedis()

        monkeypatch.setattr("app.api.global_rate_limit.get_redis", _get_redis)
        monkeypatch.setattr("app.core.rate_limit.get_redis", _get_redis)
        client = _client()

        statuses = [
            client.get("/thing").status_code for _ in range(ANONYMOUS_LIMIT * 2)
        ]
        client.get("/missing")

        assert statuses == [200] * (ANONYMOUS_LIMIT * 2)

    def test_requests_pass_when_the_cluster_client_cannot_place_a_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`RedisClusterException` is not a `RedisError`, it is caught too."""
        _use_redis(monkeypatch, ClusterBrokenRedis())
        client = _client()

        statuses = [
            client.get("/thing").status_code for _ in range(ANONYMOUS_LIMIT * 2)
        ]
        failing = client.get("/missing")

        assert statuses == [200] * (ANONYMOUS_LIMIT * 2)
        assert failing.status_code == 404

    def test_requests_pass_when_redis_was_never_initialized(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`get_redis` raising `RuntimeError` outside the lifespan is the same."""

        async def _get_redis() -> None:
            raise RuntimeError("Redis client not initialized.")

        monkeypatch.setattr("app.api.global_rate_limit.get_redis", _get_redis)
        monkeypatch.setattr("app.core.rate_limit.get_redis", _get_redis)

        assert _client().get("/thing").status_code == 200


class TestCountFailureScriptOnRealRedis:
    """The script itself, which the fake above only imitates."""

    @pytest.mark.asyncio
    async def test_it_counts_expires_and_blocks_at_the_threshold(self) -> None:
        """Skipped when no Redis answers on `REDIS_URL`."""
        client = redis_asyncio.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            await client.ping()
        except (RedisError, OSError):
            await client.aclose()
            pytest.skip("No Redis reachable on REDIS_URL")
        failures, block = _keys(Caller("ip", f"test-{uuid4()}"))
        try:
            results = [
                await client.eval(  # type: ignore[misc]
                    COUNT_FAILURE_SCRIPT, 2, failures, block, "60", "3", "300"
                )
                for _ in range(3)
            ]

            assert results == [0, 0, 1]
            assert 0 < await client.ttl(failures) <= 60
            assert 0 < await client.ttl(block) <= 300
        finally:
            await client.delete(failures, block)
            await client.aclose()
