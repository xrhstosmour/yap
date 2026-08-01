"""WebSocket endpoints for real-time communication.

Provides WebSocket support for:
- Real-time notifications
- Live system metrics streaming
- Connection health monitoring
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any
from typing import cast

from fastapi import APIRouter
from fastapi import WebSocket
from fastapi import WebSocketDisconnect

from app.core.cache import get_redis
from app.core.logging import get_logger
from app.dependencies import CurrentUserWS
from app.dependencies import SuperuserUserWS

router = APIRouter(prefix="/ws", tags=["WebSocket"])
logger = get_logger("api.ws")

_active_connections: dict[str, set[WebSocket]] = {}

# Redis pub/sub channel prefix used to fan broadcasts out across worker
# processes/replicas, since `_active_connections` only tracks sockets held
# open by the current process. See `start_broadcast_relay`.
_BROADCAST_CHANNEL_PREFIX = "ws:broadcast:"

_relay_task: asyncio.Task[None] | None = None


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
async def metrics_stream(websocket: WebSocket, _current_user: SuperuserUserWS) -> None:
    """Stream live system metrics every 2 seconds.

    Requires an authenticated superuser (see ``get_current_superuser_ws``)
    since this exposes internal DB connection-pool state.

    Args:
        websocket: Incoming WebSocket connection
        _current_user: Authenticated superuser, validated before accept
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
async def health_socket(websocket: WebSocket, _current_user: CurrentUserWS) -> None:
    """WebSocket health check endpoint.

    Requires an authenticated user (see ``get_current_user_ws``).

    Args:
        websocket: Incoming WebSocket connection
        _current_user: Authenticated user, validated before accept
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
    """Broadcast a notification to every connected WebSocket client, cluster-wide.

    Publishes to Redis rather than writing directly to `_active_connections`,
    since that dict only holds sockets accepted by *this* worker process.
    Every worker (including this one) relays the message to its own local
    connections via `_broadcast_relay`.

    Args:
        message: Notification message to broadcast
        channel: Target channel for filtering
    """
    redis_client = cast(Any, await get_redis())
    payload = json.dumps(
        {
            "type": "notification",
            "channel": channel,
            "message": message,
        }
    )
    await redis_client.publish(f"{_BROADCAST_CHANNEL_PREFIX}{channel}", payload)


async def _deliver_to_local_connections(channel: str, payload: dict[str, Any]) -> None:
    """Send `payload` to every WebSocket this process holds open for `channel`.

    Args:
        channel: Target channel
        payload: JSON-serializable message to send
    """
    connections = _active_connections.get(channel, set())
    dead: set[WebSocket] = set()

    for ws in connections:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.add(ws)

    if dead:
        _active_connections[channel] = connections - dead


async def _broadcast_relay() -> None:
    """Relay Redis pub/sub broadcasts to this worker's local connections.

    Subscribes once to every `ws:broadcast:*` channel and forwards each
    message to `_deliver_to_local_connections`. Runs for the lifetime of the
    application; started/stopped from the FastAPI lifespan.
    """
    redis_client = cast(Any, await get_redis())
    pubsub = redis_client.pubsub()
    await pubsub.psubscribe(f"{_BROADCAST_CHANNEL_PREFIX}*")

    try:
        async for message in pubsub.listen():
            if message["type"] != "pmessage":
                continue

            raw_channel = message["channel"]
            channel = raw_channel.removeprefix(_BROADCAST_CHANNEL_PREFIX)

            try:
                payload = json.loads(message["data"])
            except (TypeError, ValueError):
                logger.warning("ws_broadcast_relay_bad_payload", channel=channel)
                continue

            await _deliver_to_local_connections(channel, payload)
    finally:
        with contextlib.suppress(Exception):
            await pubsub.punsubscribe(f"{_BROADCAST_CHANNEL_PREFIX}*")
            await pubsub.aclose()


async def start_broadcast_relay() -> None:
    """Start the Redis pub/sub relay task. Call once from the app lifespan."""
    global _relay_task
    if _relay_task is None:
        _relay_task = asyncio.create_task(_broadcast_relay())


async def stop_broadcast_relay() -> None:
    """Cancel the Redis pub/sub relay task. Call once from the app lifespan."""
    global _relay_task
    if _relay_task is not None:
        _relay_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _relay_task
        _relay_task = None
