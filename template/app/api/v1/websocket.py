"""WebSocket endpoints for real-time communication.

Provides WebSocket support for:
- Real-time notifications
- Live system metrics streaming
- Connection health monitoring
"""

from __future__ import annotations

import asyncio
from typing import Any
from typing import cast

from fastapi import APIRouter
from fastapi import WebSocket
from fastapi import WebSocketDisconnect

from app.core.logging import get_logger

router = APIRouter(prefix="/ws", tags=["WebSocket"])
logger = get_logger("api.ws")

_active_connections: dict[str, set[WebSocket]] = {}


def _get_metrics() -> dict:
    """Gather current system metrics for streaming.

    Returns:
        Metrics dictionary with pool and cache stats
    """
    from app.database import async_engine

    pool = cast(Any, async_engine.pool)
    return {
        "pool": {
            "size": pool.size(),
            "checked_in": pool.checkedin(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
        },
    }


@router.websocket("/metrics")
async def metrics_stream(websocket: WebSocket) -> None:
    """Stream live system metrics every 2 seconds.

    Args:
        websocket: Incoming WebSocket connection
    """
    await websocket.accept()
    client_id = str(id(websocket))
    _active_connections.setdefault("metrics", set()).add(websocket)
    logger.info("ws_metrics_connected", client_id=client_id)

    try:
        while True:
            metrics = _get_metrics()
            await websocket.send_json(
                {
                    "type": "metrics",
                    "data": metrics,
                }
            )
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        logger.info("ws_metrics_disconnected", client_id=client_id)
    except Exception as e:
        logger.error("ws_metrics_error", error=str(e))
    finally:
        _active_connections.setdefault("metrics", set()).discard(websocket)


@router.websocket("/health")
async def health_socket(websocket: WebSocket) -> None:
    """WebSocket health check endpoint.

    Args:
        websocket: Incoming WebSocket connection
    """
    await websocket.accept()
    client_id = str(id(websocket))
    logger.info("ws_health_connected", client_id=client_id)

    try:
        while True:
            await websocket.send_json(
                {"type": "ping", "timestamp": asyncio.get_event_loop().time()}
            )
            data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
            if data == "pong":
                await websocket.send_json({"type": "pong", "ok": True})
    except TimeoutError:
        await websocket.send_json({"type": "timeout", "ok": False})
        await websocket.close()
    except WebSocketDisconnect:
        logger.info("ws_health_disconnected", client_id=client_id)


async def broadcast_notification(message: str, channel: str = "general") -> None:
    """Broadcast a notification to all connected WebSocket clients.

    Args:
        message: Notification message to broadcast
        channel: Target channel for filtering
    """
    connections = _active_connections.get(channel, set())
    dead: set[WebSocket] = set()

    for ws in connections:
        try:
            await ws.send_json(
                {
                    "type": "notification",
                    "channel": channel,
                    "message": message,
                }
            )
        except Exception:
            dead.add(ws)

    _active_connections[channel] = connections - dead
