"""Tests for WebSocket endpoints and utilities."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from uuid import UUID
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDisconnect

from app.api.v1.websocket import broadcast_notification
from app.api.v1.websocket import router as ws_router
from app.core import SYSTEM_TENANT_ID
from app.core.settings import settings
from app.core.websocket_limits import ConnectionLimiter
from app.core.ws_ticket import WebSocketTicket
from app.database import get_async_session
from app.dependencies import UserRepository
from app.models.user import User
from app.models.user import UserRole

TEST_TICKET = "test-ticket"  # noqa: S105


def _make_user(
    *,
    role: UserRole = UserRole.USER,
    is_active: bool = True,
    tenant_id: UUID | None = None,
) -> User:
    """Build an in-memory `User` for stubbing the auth dependency."""
    return User(
        id=uuid4(),
        email=f"{uuid4()}@example.com",
        hashed_password="hashed",
        is_active=is_active,
        role=role,
        tenant_id=tenant_id,
    )


def _assert_refused_with_1008(ws_client: TestClient, url: str) -> None:
    """Assert the handshake completes and the server then closes with 1008.

    A refusal before `accept` reaches a real client as an HTTP 403 with no
    close code, so the socket has to be accepted first.
    """
    with ws_client.websocket_connect(url) as websocket:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            websocket.receive_json()
    assert excinfo.value.code == 1008


def _stub_user_repository(monkeypatch: pytest.MonkeyPatch, user: User | None) -> None:
    """Make `UserRepository.get()` return `user` without touching the DB."""

    async def _get(self: UserRepository, _user_id: object) -> User | None:
        return user

    monkeypatch.setattr(UserRepository, "get", _get)


def _stub_ws_ticket(
    monkeypatch: pytest.MonkeyPatch,
    user_id: object | None,
    *,
    ticket: str = TEST_TICKET,
    token_version: int = 1,
) -> None:
    """Make `consume_ws_ticket(ticket)` resolve to `user_id` without touching Redis."""

    async def _consume(presented: str) -> WebSocketTicket | None:
        if presented != ticket:
            return None
        return None if user_id is None else WebSocketTicket(str(user_id), token_version)

    monkeypatch.setattr("app.dependencies.consume_ws_ticket", _consume)


@pytest.fixture
def ws_client() -> TestClient:
    """Create a minimal test app with only the WebSocket router mounted.

    The DB session dependency is overridden with a stub, since the auth
    dependency's DB lookup is stubbed per-test via `_stub_user_repository`.
    """
    app = FastAPI()
    # ws_router already has prefix="/ws"; mount under /api/v1
    app.include_router(ws_router, prefix="/api/v1")

    async def _fake_session() -> object:
        yield None

    app.dependency_overrides[get_async_session] = _fake_session
    return TestClient(app)


@pytest.fixture
def patch_get_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch asyncio.get_event_loop for Python 3.14 compatibility in tests."""
    mock_loop = MagicMock()
    mock_loop.time.return_value = 123456.0
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: mock_loop)


class TestWebSocketRoutes:
    """Tests for WebSocket route registration and behaviour."""

    def test_metrics_route_exists(self) -> None:
        """The /metrics WebSocket route should be registered on the router."""
        route_paths = [r.path for r in ws_router.routes]
        assert any("/metrics" in p for p in route_paths)

    def test_health_route_exists(self) -> None:
        """The /health WebSocket route should be registered on the router."""
        route_paths = [r.path for r in ws_router.routes]
        assert any("/health" in p for p in route_paths)


class TestHealthWebSocket:
    """Tests for the /ws/health WebSocket endpoint."""

    @pytest.mark.usefixtures("patch_get_event_loop")
    def test_websocket_connect_and_ping_received(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Connecting with a valid ticket should receive a ping message."""
        user = _make_user()
        _stub_user_repository(monkeypatch, user)
        _stub_ws_ticket(monkeypatch, user.id)

        with ws_client.websocket_connect(
            f"/api/v1/ws/health?ticket={TEST_TICKET}"
        ) as websocket:
            data = websocket.receive_json()
            assert data["type"] == "ping"

    @pytest.mark.usefixtures("patch_get_event_loop")
    def test_websocket_pong_roundtrip(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sending 'pong' should receive a pong confirmation."""
        user = _make_user()
        _stub_user_repository(monkeypatch, user)
        _stub_ws_ticket(monkeypatch, user.id)

        with ws_client.websocket_connect(
            f"/api/v1/ws/health?ticket={TEST_TICKET}"
        ) as websocket:
            data = websocket.receive_json()
            assert data["type"] == "ping"
            websocket.send_text("pong")
            response = websocket.receive_json()
            assert response == {"type": "pong", "ok": True}

    def test_websocket_rejects_missing_ticket(self, ws_client: TestClient) -> None:
        """Connecting without a ticket should be rejected before any data is sent."""
        _assert_refused_with_1008(ws_client, "/api/v1/ws/health")

    def test_websocket_rejects_invalid_ticket(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Connecting with an unrecognized ticket should be rejected."""
        _stub_ws_ticket(monkeypatch, None)

        _assert_refused_with_1008(
            ws_client, "/api/v1/ws/health?ticket=not-a-real-ticket"
        )

    def test_websocket_rejects_inactive_user(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ticket for an inactive user should be rejected."""
        user = _make_user(is_active=False)
        _stub_user_repository(monkeypatch, user)
        _stub_ws_ticket(monkeypatch, user.id)

        _assert_refused_with_1008(ws_client, f"/api/v1/ws/health?ticket={TEST_TICKET}")

    def test_websocket_rejects_a_ticket_from_before_a_revoke(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ticket minted before a `token_version` bump is refused."""
        user = _make_user()
        _stub_user_repository(monkeypatch, user)
        _stub_ws_ticket(monkeypatch, user.id, token_version=user.token_version - 1)

        _assert_refused_with_1008(ws_client, f"/api/v1/ws/health?ticket={TEST_TICKET}")

    def test_websocket_rejects_unknown_user(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ticket for a user that no longer exists should be rejected."""
        _stub_user_repository(monkeypatch, None)
        _stub_ws_ticket(monkeypatch, uuid4())

        _assert_refused_with_1008(ws_client, f"/api/v1/ws/health?ticket={TEST_TICKET}")


class TestMetricsWebSocket:
    """Tests for the /ws/metrics WebSocket endpoint."""

    def test_websocket_rejects_missing_ticket(self, ws_client: TestClient) -> None:
        """Connecting without a ticket should be rejected before any data is sent."""
        _assert_refused_with_1008(ws_client, "/api/v1/ws/metrics")

    def test_websocket_rejects_non_superuser(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An authenticated but non-superuser ticket should be rejected."""
        user = _make_user(role=UserRole.USER)
        _stub_user_repository(monkeypatch, user)
        _stub_ws_ticket(monkeypatch, user.id)

        _assert_refused_with_1008(ws_client, f"/api/v1/ws/metrics?ticket={TEST_TICKET}")

    def test_websocket_connect_as_superuser_receives_metrics(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A superuser ticket should be accepted and stream metrics."""
        user = _make_user(role=UserRole.SUPERUSER, tenant_id=SYSTEM_TENANT_ID)
        _stub_user_repository(monkeypatch, user)
        _stub_ws_ticket(monkeypatch, user.id)

        with ws_client.websocket_connect(
            f"/api/v1/ws/metrics?ticket={TEST_TICKET}"
        ) as websocket:
            data = websocket.receive_json()
            assert data["type"] == "metrics"
            assert "pool" in data["data"]


@pytest.fixture(autouse=True)
def _fresh_connection_limiter(monkeypatch: pytest.MonkeyPatch) -> ConnectionLimiter:
    """Give every test its own socket counts, the real ones are per process."""
    limiter = ConnectionLimiter()
    monkeypatch.setattr("app.api.v1.websocket.connection_limiter", limiter)
    return limiter


class TestConnectionCaps:
    """A socket past a connection cap is closed with 1013 right after the handshake."""

    @pytest.mark.usefixtures("patch_get_event_loop")
    def test_the_fourth_socket_of_a_user_is_refused(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The default is three sockets per user, close code 1013 for the next."""
        user = _make_user()
        _stub_user_repository(monkeypatch, user)
        _stub_ws_ticket(monkeypatch, user.id)
        url = f"/api/v1/ws/health?ticket={TEST_TICKET}"

        with (
            ws_client.websocket_connect(url) as first,
            ws_client.websocket_connect(url) as second,
            ws_client.websocket_connect(url) as third,
        ):
            for socket in (first, second, third):
                assert socket.receive_json()["type"] == "ping"
            with pytest.raises(WebSocketDisconnect) as excinfo:
                with ws_client.websocket_connect(url) as refused:
                    refused.receive_json()

        assert excinfo.value.code == 1013

    @pytest.mark.usefixtures("patch_get_event_loop")
    def test_the_worker_total_is_capped_across_users(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Different users share the per-worker total."""
        monkeypatch.setattr(settings, "WS_MAX_CONNECTIONS_TOTAL", 1)
        users = iter([_make_user(), _make_user()])
        current: dict[str, User] = {}

        async def _get(self: UserRepository, _user_id: object) -> User:
            return current["user"]

        monkeypatch.setattr(UserRepository, "get", _get)
        _stub_ws_ticket(monkeypatch, uuid4())
        url = f"/api/v1/ws/health?ticket={TEST_TICKET}"

        current["user"] = next(users)
        with ws_client.websocket_connect(url) as first:
            assert first.receive_json()["type"] == "ping"
            current["user"] = next(users)
            with pytest.raises(WebSocketDisconnect) as excinfo:
                with ws_client.websocket_connect(url) as refused:
                    refused.receive_json()

        assert excinfo.value.code == 1013

    @pytest.mark.usefixtures("patch_get_event_loop")
    def test_a_slot_is_released_when_the_socket_closes(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Closing sockets frees their slots, so the user can connect again."""
        user = _make_user()
        _stub_user_repository(monkeypatch, user)
        _stub_ws_ticket(monkeypatch, user.id)
        url = f"/api/v1/ws/health?ticket={TEST_TICKET}"

        for _ in range(settings.WS_MAX_CONNECTIONS_PER_USER + 2):
            with ws_client.websocket_connect(url) as socket:
                assert socket.receive_json()["type"] == "ping"


class TestAdminConnectionCap:
    """Admin metrics sockets have a cap of their own, apart from the total."""

    def test_a_full_total_does_not_lock_the_admins_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ordinary users filling the total leave the admin slots free."""
        monkeypatch.setattr(settings, "WS_MAX_CONNECTIONS_TOTAL", 1)
        limiter = ConnectionLimiter()

        assert limiter.acquire(uuid4())
        assert not limiter.acquire(uuid4())
        assert limiter.acquire(uuid4(), admin=True)

    def test_admin_sockets_do_not_use_up_the_total(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Admin sockets leave the total for everyone else."""
        monkeypatch.setattr(settings, "WS_MAX_CONNECTIONS_TOTAL", 1)
        limiter = ConnectionLimiter()

        assert limiter.acquire(uuid4(), admin=True)
        assert limiter.acquire(uuid4())

    def test_the_admin_cap_is_enforced_and_released(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Past `WS_MAX_ADMIN_CONNECTIONS` is refused, closing frees a slot."""
        monkeypatch.setattr(settings, "WS_MAX_ADMIN_CONNECTIONS", 1)
        limiter = ConnectionLimiter()
        first, second = uuid4(), uuid4()

        assert limiter.acquire(first, admin=True)
        assert not limiter.acquire(second, admin=True)
        limiter.release(first, admin=True)
        assert limiter.acquire(second, admin=True)

    def test_the_per_user_cap_still_applies_to_an_admin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One admin cannot hold every admin slot."""
        monkeypatch.setattr(settings, "WS_MAX_CONNECTIONS_PER_USER", 1)
        limiter = ConnectionLimiter()
        admin_id = uuid4()

        assert limiter.acquire(admin_id, admin=True)
        assert not limiter.acquire(admin_id, admin=True)


class TestSocketLimits:
    """A socket cannot outlive its lifetime or send an oversized message."""

    @pytest.mark.usefixtures("patch_get_event_loop")
    def test_an_oversized_message_closes_the_socket(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """More than `WS_MAX_MESSAGE_BYTES` is close code 1009."""
        user = _make_user()
        _stub_user_repository(monkeypatch, user)
        _stub_ws_ticket(monkeypatch, user.id)

        with ws_client.websocket_connect(
            f"/api/v1/ws/health?ticket={TEST_TICKET}"
        ) as websocket:
            websocket.receive_json()
            websocket.send_text("x" * (settings.WS_MAX_MESSAGE_BYTES + 1))
            with pytest.raises(WebSocketDisconnect) as excinfo:
                websocket.receive_json()

        assert excinfo.value.code == 1009

    @pytest.mark.usefixtures("patch_get_event_loop")
    def test_a_message_at_the_limit_is_served(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exactly `WS_MAX_MESSAGE_BYTES` is still within the limit."""
        user = _make_user()
        _stub_user_repository(monkeypatch, user)
        _stub_ws_ticket(monkeypatch, user.id)

        with ws_client.websocket_connect(
            f"/api/v1/ws/health?ticket={TEST_TICKET}"
        ) as websocket:
            websocket.receive_json()
            websocket.send_text("x" * settings.WS_MAX_MESSAGE_BYTES)
            assert websocket.receive_json()["type"] == "ping"
            websocket.send_text("pong")
            assert websocket.receive_json() == {"type": "pong", "ok": True}

    @pytest.mark.usefixtures("patch_get_event_loop")
    @pytest.mark.parametrize("endpoint", ["health", "metrics"])
    def test_a_socket_is_closed_at_its_maximum_lifetime(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch, endpoint: str
    ) -> None:
        """Past `WS_MAX_LIFETIME_SECONDS` the server ends the socket."""
        monkeypatch.setattr(settings, "WS_MAX_LIFETIME_SECONDS", 0)
        user = _make_user(role=UserRole.SUPERUSER, tenant_id=SYSTEM_TENANT_ID)
        _stub_user_repository(monkeypatch, user)
        _stub_ws_ticket(monkeypatch, user.id)

        with ws_client.websocket_connect(
            f"/api/v1/ws/{endpoint}?ticket={TEST_TICKET}"
        ) as websocket:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                websocket.receive_json()

        assert excinfo.value.code == 1000


class TestSessionIsNotHeldWhileConnected:
    """The user lookup gives its pool connection back before the loop."""

    @pytest.mark.usefixtures("patch_get_event_loop")
    def test_the_session_is_closed_before_the_first_message(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No session stays open while the socket is."""
        user = _make_user()
        _stub_user_repository(monkeypatch, user)
        _stub_ws_ticket(monkeypatch, user.id)
        events: list[str] = []

        @contextlib.asynccontextmanager
        async def _recording_scope() -> AsyncIterator[None]:
            events.append("opened")
            try:
                yield None
            finally:
                events.append("closed")

        monkeypatch.setattr("app.dependencies.session_scope", _recording_scope)

        with ws_client.websocket_connect(
            f"/api/v1/ws/health?ticket={TEST_TICKET}"
        ) as websocket:
            websocket.receive_json()
            assert events == ["opened", "closed"]


class TestMetricsRevalidation:
    """A platform admin who loses the role loses the stream."""

    def test_a_demoted_admin_is_closed(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The next re-check finds a plain user and ends the socket with 1008."""
        monkeypatch.setattr("app.api.v1.websocket._METRICS_REVALIDATE_EVERY", 1)
        monkeypatch.setattr("app.api.v1.websocket._METRICS_INTERVAL_SECONDS", 0.01)
        admin = _make_user(role=UserRole.SUPERUSER, tenant_id=SYSTEM_TENANT_ID)
        demoted = User(
            id=admin.id,
            email=admin.email,
            hashed_password="hashed",
            is_active=True,
            role=UserRole.USER,
            tenant_id=SYSTEM_TENANT_ID,
        )
        lookups = [admin, demoted]

        async def _get(self: UserRepository, _user_id: object) -> User:
            return lookups.pop(0) if len(lookups) > 1 else lookups[0]

        monkeypatch.setattr(UserRepository, "get", _get)
        _stub_ws_ticket(monkeypatch, admin.id)

        with ws_client.websocket_connect(
            f"/api/v1/ws/metrics?ticket={TEST_TICKET}"
        ) as websocket:
            assert websocket.receive_json()["type"] == "metrics"
            with pytest.raises(WebSocketDisconnect) as excinfo:
                while True:
                    websocket.receive_json()

        assert excinfo.value.code == 1008

    def test_an_admin_whose_tokens_were_revoked_is_closed(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bumped `token_version` ends the stream at the next re-check."""
        monkeypatch.setattr("app.api.v1.websocket._METRICS_REVALIDATE_EVERY", 1)
        monkeypatch.setattr("app.api.v1.websocket._METRICS_INTERVAL_SECONDS", 0.01)
        admin = _make_user(role=UserRole.SUPERUSER, tenant_id=SYSTEM_TENANT_ID)
        revoked = User(
            id=admin.id,
            email=admin.email,
            hashed_password="hashed",
            is_active=True,
            role=UserRole.SUPERUSER,
            tenant_id=SYSTEM_TENANT_ID,
            token_version=admin.token_version + 1,
        )
        lookups = [admin, revoked]

        async def _get(self: UserRepository, _user_id: object) -> User:
            return lookups.pop(0) if len(lookups) > 1 else lookups[0]

        monkeypatch.setattr(UserRepository, "get", _get)
        _stub_ws_ticket(monkeypatch, admin.id, token_version=admin.token_version)

        with ws_client.websocket_connect(
            f"/api/v1/ws/metrics?ticket={TEST_TICKET}"
        ) as websocket:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                while True:
                    websocket.receive_json()

        assert excinfo.value.code == 1008

    def test_an_admin_who_is_deactivated_is_closed(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deactivation ends the stream the same way."""
        monkeypatch.setattr("app.api.v1.websocket._METRICS_REVALIDATE_EVERY", 1)
        monkeypatch.setattr("app.api.v1.websocket._METRICS_INTERVAL_SECONDS", 0.01)
        admin = _make_user(role=UserRole.SUPERUSER, tenant_id=SYSTEM_TENANT_ID)
        inactive = _make_user(
            role=UserRole.SUPERUSER, tenant_id=SYSTEM_TENANT_ID, is_active=False
        )
        lookups = [admin, inactive]

        async def _get(self: UserRepository, _user_id: object) -> User:
            return lookups.pop(0) if len(lookups) > 1 else lookups[0]

        monkeypatch.setattr(UserRepository, "get", _get)
        _stub_ws_ticket(monkeypatch, admin.id)

        with ws_client.websocket_connect(
            f"/api/v1/ws/metrics?ticket={TEST_TICKET}"
        ) as websocket:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                while True:
                    websocket.receive_json()

        assert excinfo.value.code == 1008


class TestBroadcastNotification:
    """Tests for broadcast_notification() publishing to Redis."""

    @pytest.mark.anyio
    async def test_publishes_to_redis_channel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """broadcast_notification() should publish a JSON payload to the channel."""
        mock_redis = AsyncMock()
        monkeypatch.setattr(
            "app.api.v1.websocket.get_redis", AsyncMock(return_value=mock_redis)
        )

        await broadcast_notification("Hello, world!", channel="general")

        mock_redis.publish.assert_called_once()
        channel_arg, payload_arg = mock_redis.publish.call_args.args
        assert channel_arg == "ws:broadcast:general"
        assert json.loads(payload_arg) == {
            "type": "notification",
            "channel": "general",
            "message": "Hello, world!",
        }


class TestDeliverToLocalConnections:
    """Tests for _deliver_to_local_connections(), the local-fan-out half of a broadcast."""

    @pytest.mark.anyio
    async def test_sends_to_all_connected_clients(self) -> None:
        """Every local socket in the channel should receive the payload."""
        from app.api.v1 import websocket as ws_module

        mock_ws_1 = AsyncMock()
        mock_ws_2 = AsyncMock()

        original_connections = dict(ws_module._active_connections)
        try:
            ws_module._active_connections["general"] = {mock_ws_1, mock_ws_2}

            payload = {"type": "notification", "channel": "general", "message": "hi"}
            await ws_module._deliver_to_local_connections("general", payload)

            mock_ws_1.send_json.assert_called_once_with(payload)
            mock_ws_2.send_json.assert_called_once_with(payload)
        finally:
            ws_module._active_connections = original_connections

    @pytest.mark.anyio
    async def test_removes_dead_connections(self) -> None:
        """Failing connections should be removed from the active set."""
        from app.api.v1 import websocket as ws_module

        mock_good = AsyncMock()
        mock_bad = AsyncMock()
        mock_bad.send_json.side_effect = RuntimeError("connection lost")

        original_connections = dict(ws_module._active_connections)
        try:
            ws_module._active_connections["general"] = {mock_good, mock_bad}

            payload = {"type": "notification", "channel": "general", "message": "hi"}
            await ws_module._deliver_to_local_connections("general", payload)

            mock_good.send_json.assert_called_once()
            assert mock_bad not in ws_module._active_connections["general"]
            assert mock_good in ws_module._active_connections["general"]
        finally:
            ws_module._active_connections = original_connections


class TestDeliverySurvivesConcurrentConnectionChanges:
    """A connect or disconnect mid-broadcast must not kill the relay.

    Every `send_json` yields to the event loop, and the socket endpoints
    add to and discard from the very set being iterated. Iterating it
    directly raised `RuntimeError: Set changed size during iteration` out
    of the `for` itself, past the `try` that only guards the send, which
    ended the relay task for the lifetime of the process.
    """

    @pytest.mark.anyio
    async def test_a_client_connecting_mid_broadcast_does_not_raise(self) -> None:
        """Mutating the set during a send must be tolerated."""
        from app.api.v1 import websocket as ws_module

        joiner = AsyncMock()
        first = AsyncMock()

        original_connections = dict(ws_module._active_connections)
        try:
            live: set[Any] = {first}
            ws_module._active_connections["general"] = live

            # Exactly what an endpoint does while the relay is awaiting.
            async def _send_and_join(payload: dict[str, Any]) -> None:
                live.add(joiner)

            first.send_json.side_effect = _send_and_join

            await ws_module._deliver_to_local_connections("general", {"a": 1})

            # And the newcomer is still registered afterwards.
            assert joiner in ws_module._active_connections["general"]
        finally:
            ws_module._active_connections = original_connections

    @pytest.mark.anyio
    async def test_a_client_disconnecting_mid_broadcast_does_not_raise(self) -> None:
        """Removal during a send must be tolerated too."""
        from app.api.v1 import websocket as ws_module

        leaver = AsyncMock()
        first = AsyncMock()

        original_connections = dict(ws_module._active_connections)
        try:
            live: set[Any] = {first, leaver}
            ws_module._active_connections["general"] = live

            async def _send_and_leave(payload: dict[str, Any]) -> None:
                live.discard(leaver)

            first.send_json.side_effect = _send_and_leave

            await ws_module._deliver_to_local_connections("general", {"a": 1})
        finally:
            ws_module._active_connections = original_connections

    @pytest.mark.anyio
    async def test_pruning_keeps_a_client_that_joined_during_the_fan_out(
        self,
    ) -> None:
        """Pruning must not resurrect a stale snapshot over the live set.

        Rebinding to `connections - dead` rebuilt the set from a snapshot
        taken before the awaits, so a socket that connected during them was
        silently dropped from the registry and never saw another broadcast.
        """
        from app.api.v1 import websocket as ws_module

        joiner = AsyncMock()
        good = AsyncMock()
        bad = AsyncMock()

        original_connections = dict(ws_module._active_connections)
        try:
            live: set[Any] = {good, bad}
            ws_module._active_connections["general"] = live

            async def _send_and_join(payload: dict[str, Any]) -> None:
                live.add(joiner)

            good.send_json.side_effect = _send_and_join
            bad.send_json.side_effect = RuntimeError("connection lost")

            await ws_module._deliver_to_local_connections("general", {"a": 1})

            remaining = ws_module._active_connections["general"]
            assert joiner in remaining
            assert good in remaining
            assert bad not in remaining
        finally:
            ws_module._active_connections = original_connections


class _FakePubSub:
    """Minimal stand-in for `redis.asyncio.client.PubSub` used by the relay tests."""

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._messages = messages
        self.psubscribe = AsyncMock()
        self.punsubscribe = AsyncMock()
        self.aclose = AsyncMock()

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        for message in self._messages:
            yield message


class TestBroadcastRelay:
    """Tests for _broadcast_relay(), the Redis pub/sub -> local-socket bridge."""

    @pytest.mark.anyio
    async def test_relays_pmessage_to_local_connections(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pmessage on a broadcast channel should be delivered locally."""
        from app.api.v1 import websocket as ws_module

        payload = {"type": "notification", "channel": "general", "message": "hi"}
        fake_pubsub = _FakePubSub(
            [
                {
                    "type": "pmessage",
                    "channel": "ws:broadcast:general",
                    "data": json.dumps(payload),
                }
            ]
        )
        mock_redis = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=fake_pubsub)
        monkeypatch.setattr(
            "app.api.v1.websocket.get_redis", AsyncMock(return_value=mock_redis)
        )

        delivered: list[tuple[str, dict[str, Any]]] = []

        async def _fake_deliver(channel: str, message: dict[str, Any]) -> None:
            delivered.append((channel, message))

        monkeypatch.setattr(ws_module, "_deliver_to_local_connections", _fake_deliver)

        await ws_module._broadcast_relay()

        assert delivered == [("general", payload)]
        fake_pubsub.psubscribe.assert_awaited_once_with("ws:broadcast:*")

    @pytest.mark.anyio
    async def test_ignores_non_pmessage_and_bad_payload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Subscribe confirmations and malformed payloads should not be delivered."""
        from app.api.v1 import websocket as ws_module

        fake_pubsub = _FakePubSub(
            [
                {"type": "psubscribe", "channel": None, "data": 1},
                {
                    "type": "pmessage",
                    "channel": "ws:broadcast:general",
                    "data": "not-json",
                },
            ]
        )
        mock_redis = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=fake_pubsub)
        monkeypatch.setattr(
            "app.api.v1.websocket.get_redis", AsyncMock(return_value=mock_redis)
        )

        delivered: list[Any] = []
        monkeypatch.setattr(
            ws_module,
            "_deliver_to_local_connections",
            AsyncMock(side_effect=lambda *args: delivered.append(args)),
        )

        await ws_module._broadcast_relay()

        assert delivered == []


class TestBroadcastRelayLifecycle:
    """Tests for start_broadcast_relay()/stop_broadcast_relay() task management."""

    @pytest.mark.anyio
    async def test_start_is_idempotent_and_stop_cancels(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A second start() should not spawn a second task; stop() cancels it."""
        from app.api.v1 import websocket as ws_module

        async def _hang_forever() -> None:
            await asyncio.Event().wait()

        monkeypatch.setattr(ws_module, "_broadcast_relay", _hang_forever)
        assert ws_module._relay_task is None

        try:
            await ws_module.start_broadcast_relay()
            first_task = ws_module._relay_task
            assert first_task is not None

            await ws_module.start_broadcast_relay()
            assert ws_module._relay_task is first_task
        finally:
            await ws_module.stop_broadcast_relay()

        assert ws_module._relay_task is None
        assert first_task.cancelled()
