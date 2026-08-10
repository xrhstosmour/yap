"""Tests for WebSocket endpoints and utilities."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDisconnect

from app.api.v1.websocket import broadcast_notification
from app.api.v1.websocket import router as ws_router
from app.database import get_async_session
from app.dependencies import UserRepository
from app.models.user import User
from app.models.user import UserRole

TEST_TICKET = "test-ticket"  # noqa: S105


def _make_user(*, role: UserRole = UserRole.USER, is_active: bool = True) -> User:
    """Build an in-memory `User` for stubbing the auth dependency."""
    return User(
        id=uuid4(),
        email=f"{uuid4()}@example.com",
        hashed_password="hashed",
        is_active=is_active,
        role=role,
        tenant_id=None,
    )


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
) -> None:
    """Make `consume_ws_ticket(ticket)` resolve to `user_id` without touching Redis."""

    async def _consume(presented: str) -> str | None:
        if presented != ticket:
            return None
        return None if user_id is None else str(user_id)

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
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with ws_client.websocket_connect("/api/v1/ws/health"):
                pass
        assert excinfo.value.code == 1008

    def test_websocket_rejects_invalid_ticket(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Connecting with an unrecognized ticket should be rejected."""
        _stub_ws_ticket(monkeypatch, None)

        with pytest.raises(WebSocketDisconnect) as excinfo:
            with ws_client.websocket_connect(
                "/api/v1/ws/health?ticket=not-a-real-ticket"
            ):
                pass
        assert excinfo.value.code == 1008

    def test_websocket_rejects_inactive_user(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ticket for an inactive user should be rejected."""
        user = _make_user(is_active=False)
        _stub_user_repository(monkeypatch, user)
        _stub_ws_ticket(monkeypatch, user.id)

        with pytest.raises(WebSocketDisconnect) as excinfo:
            with ws_client.websocket_connect(f"/api/v1/ws/health?ticket={TEST_TICKET}"):
                pass
        assert excinfo.value.code == 1008

    def test_websocket_rejects_unknown_user(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ticket for a user that no longer exists should be rejected."""
        _stub_user_repository(monkeypatch, None)
        _stub_ws_ticket(monkeypatch, uuid4())

        with pytest.raises(WebSocketDisconnect) as excinfo:
            with ws_client.websocket_connect(f"/api/v1/ws/health?ticket={TEST_TICKET}"):
                pass
        assert excinfo.value.code == 1008


class TestMetricsWebSocket:
    """Tests for the /ws/metrics WebSocket endpoint."""

    def test_websocket_rejects_missing_ticket(self, ws_client: TestClient) -> None:
        """Connecting without a ticket should be rejected before any data is sent."""
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with ws_client.websocket_connect("/api/v1/ws/metrics"):
                pass
        assert excinfo.value.code == 1008

    def test_websocket_rejects_non_superuser(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An authenticated but non-superuser ticket should be rejected."""
        user = _make_user(role=UserRole.USER)
        _stub_user_repository(monkeypatch, user)
        _stub_ws_ticket(monkeypatch, user.id)

        with pytest.raises(WebSocketDisconnect) as excinfo:
            with ws_client.websocket_connect(
                f"/api/v1/ws/metrics?ticket={TEST_TICKET}"
            ):
                pass
        assert excinfo.value.code == 1008

    def test_websocket_connect_as_superuser_receives_metrics(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A superuser ticket should be accepted and stream metrics."""
        user = _make_user(role=UserRole.SUPERUSER)
        _stub_user_repository(monkeypatch, user)
        _stub_ws_ticket(monkeypatch, user.id)

        with ws_client.websocket_connect(
            f"/api/v1/ws/metrics?ticket={TEST_TICKET}"
        ) as websocket:
            data = websocket.receive_json()
            assert data["type"] == "metrics"
            assert "pool" in data["data"]


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
