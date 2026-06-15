"""Tests for WebSocket endpoints and utilities."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.websocket import broadcast_notification
from app.api.v1.websocket import router as ws_router


@pytest.fixture
def ws_client() -> TestClient:
    """Create a minimal test app with only the WebSocket router mounted."""
    app = FastAPI()
    # ws_router already has prefix="/ws"; mount under /api/v1
    app.include_router(ws_router, prefix="/api/v1")
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
    def test_websocket_connect_and_ping_received(self, ws_client: TestClient) -> None:
        """Connecting to /ws/health should receive a ping message."""
        with ws_client.websocket_connect("/api/v1/ws/health") as websocket:
            data = websocket.receive_json()
            assert data["type"] == "ping"

    @pytest.mark.usefixtures("patch_get_event_loop")
    def test_websocket_pong_roundtrip(self, ws_client: TestClient) -> None:
        """Sending 'pong' should receive a pong confirmation."""
        with ws_client.websocket_connect("/api/v1/ws/health") as websocket:
            data = websocket.receive_json()
            assert data["type"] == "ping"
            websocket.send_text("pong")
            response = websocket.receive_json()
            assert response == {"type": "pong", "ok": True}


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
