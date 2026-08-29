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

    Iterates a snapshot, and prunes with `discard` on the live set rather
    than rebinding it. Every `send_json` below yields to the event loop, and
    the socket endpoints add to and discard from that same set object, so a
    client connecting or disconnecting mid-broadcast used to raise
    `RuntimeError: Set changed size during iteration` straight out of the
    `for` (the `try` only guards the send). That escaped `_broadcast_relay`,
    ran its `finally`, and killed the relay task for the lifetime of the
    process, with `_relay_task` left non-None so nothing restarted it. One
    ordinary connect during one broadcast silently ended notifications for
    that worker.

    Rebinding to `connections - dead` was wrong for the same reason: the
    snapshot predates any socket that connected during the awaits, so the
    assignment dropped it from the registry.

    Args:
        channel: Target channel
        payload: JSON-serializable message to send
    """
    connections = _active_connections.get(channel)
    if not connections:
        return

    dead: list[WebSocket] = []

    for ws in tuple(connections):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)

    for ws in dead:
        connections.discard(ws)


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

            # Belt and braces around the snapshot fix in
            # `_deliver_to_local_connections`. This task has no supervisor:
            # anything that escapes here ends broadcasts for the whole
            # process until it restarts, and `_relay_task` stays non-None so
            # `start_broadcast_relay` will not bring it back. One bad
            # delivery must not be able to do that.
            try:
                await _deliver_to_local_connections(channel, payload)
            except Exception:
                logger.exception("ws_broadcast_relay_delivery_failed", channel=channel)
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
