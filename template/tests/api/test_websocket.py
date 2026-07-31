"""Tests for WebSocket endpoints and utilities."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDisconnect

from app.api.v1.websocket import broadcast_notification
from app.api.v1.websocket import router as ws_router
from app.core.security import create_access_token
from app.database import get_async_session
from app.dependencies import UserRepository
from app.models.user import User
from app.models.user import UserRole


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
        """Connecting with a valid token should receive a ping message."""
        user = _make_user()
        _stub_user_repository(monkeypatch, user)
        token = create_access_token(user.id)

        with ws_client.websocket_connect(
            f"/api/v1/ws/health?token={token}"
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
        token = create_access_token(user.id)

        with ws_client.websocket_connect(
            f"/api/v1/ws/health?token={token}"
        ) as websocket:
            data = websocket.receive_json()
            assert data["type"] == "ping"
            websocket.send_text("pong")
            response = websocket.receive_json()
            assert response == {"type": "pong", "ok": True}

    def test_websocket_rejects_missing_token(self, ws_client: TestClient) -> None:
        """Connecting without a token should be rejected before any data is sent."""
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with ws_client.websocket_connect("/api/v1/ws/health"):
                pass
        assert excinfo.value.code == 1008

    def test_websocket_rejects_invalid_token(self, ws_client: TestClient) -> None:
        """Connecting with a malformed token should be rejected."""
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with ws_client.websocket_connect(
                "/api/v1/ws/health?token=not-a-real-token"
            ):
                pass
        assert excinfo.value.code == 1008

    def test_websocket_rejects_inactive_user(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A token for an inactive user should be rejected."""
        user = _make_user(is_active=False)
        _stub_user_repository(monkeypatch, user)
        token = create_access_token(user.id)

        with pytest.raises(WebSocketDisconnect) as excinfo:
            with ws_client.websocket_connect(f"/api/v1/ws/health?token={token}"):
                pass
        assert excinfo.value.code == 1008

    def test_websocket_rejects_unknown_user(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A token for a user that no longer exists should be rejected."""
        _stub_user_repository(monkeypatch, None)
        token = create_access_token(uuid4())

        with pytest.raises(WebSocketDisconnect) as excinfo:
            with ws_client.websocket_connect(f"/api/v1/ws/health?token={token}"):
                pass
        assert excinfo.value.code == 1008


class TestMetricsWebSocket:
    """Tests for the /ws/metrics WebSocket endpoint."""

    def test_websocket_rejects_missing_token(self, ws_client: TestClient) -> None:
        """Connecting without a token should be rejected before any data is sent."""
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with ws_client.websocket_connect("/api/v1/ws/metrics"):
                pass
        assert excinfo.value.code == 1008

    def test_websocket_rejects_non_superuser(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An authenticated but non-superuser token should be rejected."""
        user = _make_user(role=UserRole.USER)
        _stub_user_repository(monkeypatch, user)
        token = create_access_token(user.id)

        with pytest.raises(WebSocketDisconnect) as excinfo:
            with ws_client.websocket_connect(f"/api/v1/ws/metrics?token={token}"):
                pass
        assert excinfo.value.code == 1008

    def test_websocket_connect_as_superuser_receives_metrics(
        self, ws_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A superuser token should be accepted and stream metrics."""
        user = _make_user(role=UserRole.SUPERUSER)
        _stub_user_repository(monkeypatch, user)
        token = create_access_token(user.id)

        with ws_client.websocket_connect(
            f"/api/v1/ws/metrics?token={token}"
        ) as websocket:
            data = websocket.receive_json()
            assert data["type"] == "metrics"
            assert "pool" in data["data"]


class TestBroadcastNotification:
    """Tests for the broadcast_notification() utility."""

    @pytest.mark.anyio
    async def test_sends_to_all_connected_clients(self) -> None:
        """broadcast_notification() should send JSON to every client in the channel."""
        from app.api.v1 import websocket as ws_module

        mock_ws_1 = AsyncMock()
        mock_ws_2 = AsyncMock()

        original_connections = dict(ws_module._active_connections)
        try:
            ws_module._active_connections["general"] = {mock_ws_1, mock_ws_2}

            await broadcast_notification("Hello, world!", channel="general")

            expected_payload = {
                "type": "notification",
                "channel": "general",
                "message": "Hello, world!",
            }
            mock_ws_1.send_json.assert_called_once_with(expected_payload)
            mock_ws_2.send_json.assert_called_once_with(expected_payload)
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

            await broadcast_notification("hi", channel="general")

            # The good connection should have received the message.
            mock_good.send_json.assert_called_once()
            # The bad connection should be removed.
            assert mock_bad not in ws_module._active_connections["general"]
            assert mock_good in ws_module._active_connections["general"]
        finally:
            ws_module._active_connections = original_connections
