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
import time
from typing import Any
from typing import cast
from uuid import UUID

from fastapi import APIRouter
from fastapi import WebSocket
from fastapi import WebSocketDisconnect
from fastapi import status

from app.core.cache import get_redis
from app.core.logging import get_logger
from app.core.settings import settings
from app.core.websocket_limits import connection_limiter
from app.dependencies import CurrentUserWS
from app.dependencies import PlatformAdminUserWS
from app.dependencies import is_usable_platform_admin
from app.dependencies import load_user_for_websocket
from app.models.user import User

router = APIRouter(prefix="/ws", tags=["WebSocket"])
logger = get_logger("api.ws")

_active_connections: dict[str, set[WebSocket]] = {}

# Redis pub/sub channel prefix used to fan broadcasts out across worker
# processes/replicas, since `_active_connections` only tracks sockets held
# open by the current process. See `start_broadcast_relay`.
_BROADCAST_CHANNEL_PREFIX = "ws:broadcast:"

_relay_task: asyncio.Task[None] | None = None

# A metrics frame goes out this often, and the admin is looked up again every
# `_METRICS_REVALIDATE_EVERY` of them, about 30 seconds.
_METRICS_INTERVAL_SECONDS = 2
_METRICS_REVALIDATE_EVERY = 15

# How long a health socket may stay silent before it is told it timed out.
_HEALTH_IDLE_SECONDS = 30


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


async def _refuse_if_full(
    websocket: WebSocket, user: User, *, admin: bool = False
) -> bool:
    """Accept and at once close a socket when a connection cap is reached.

    The close code only reaches the client after the handshake, a close
    before `accept` is answered with an HTTP 403 and the code is lost.

    The ticket is already spent by the time the cap is known, because the
    user, and so which cap applies, is only learnt from it. A refused client
    mints another, which is one cheap request.

    Args:
        websocket: The incoming, not yet accepted, connection.
        user: Who is connecting.
        admin: Whether this is a platform admin metrics socket.

    Returns:
        True when the socket was refused and the handler must return, False
        when a slot was taken, which the handler must give back with
        `connection_limiter.release`.
    """
    if connection_limiter.acquire(user.id, admin=admin):
        return False
    logger.warning("ws_connection_cap_reached", user_id=str(user.id))
    await websocket.accept()
    await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
    return True


@router.websocket("/metrics")
async def metrics_stream(
    websocket: WebSocket, current_user: PlatformAdminUserWS
) -> None:
    """Stream live system metrics every 2 seconds.

    Requires an authenticated platform admin (see ``get_platform_admin_ws``)
    since this exposes internal DB connection-pool state. The admin is
    looked up again every `_METRICS_REVALIDATE_EVERY` messages, so a demoted
    or deactivated one loses the stream within about 30 seconds instead of
    keeping it until the socket drops. A socket is closed after
    `WS_MAX_LIFETIME_SECONDS` regardless.

    Args:
        websocket: Incoming WebSocket connection
        current_user: Authenticated platform admin, validated before accept
    """
    if await _refuse_if_full(websocket, current_user, admin=True):
        return

    client_id = str(id(websocket))
    deadline = time.monotonic() + settings.WS_MAX_LIFETIME_SECONDS
    iteration = 0
    try:
        await websocket.accept()
        _active_connections.setdefault("metrics", set()).add(websocket)
        logger.info("ws_metrics_connected", client_id=client_id)

        while time.monotonic() < deadline:
            if iteration and iteration % _METRICS_REVALIDATE_EVERY == 0:
                if not await _is_still_platform_admin(
                    current_user.id, current_user.token_version
                ):
                    logger.info("ws_metrics_revoked", client_id=client_id)
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                    return
            iteration += 1

            metrics = _get_metrics()
            await websocket.send_json(
                {
                    "type": "metrics",
                    "data": metrics,
                }
            )
            await asyncio.sleep(_METRICS_INTERVAL_SECONDS)
        await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
    except WebSocketDisconnect:
        logger.info("ws_metrics_disconnected", client_id=client_id)
    except Exception as e:
        logger.error("ws_metrics_error", error=str(e))
    finally:
        _active_connections.setdefault("metrics", set()).discard(websocket)
        connection_limiter.release(current_user.id, admin=True)


async def _is_still_platform_admin(user_id: UUID, token_version: int) -> bool:
    """Look a platform admin up again and say whether they still are one.

    Args:
        user_id: The admin the socket was opened for.
        token_version: The `token_version` the socket was opened with.

    Returns:
        False when the user is gone, no longer a usable platform admin, or
        their tokens were revoked since the socket was opened.
    """
    user = await load_user_for_websocket(user_id)
    return (
        user is not None
        and user.token_version == token_version
        and is_usable_platform_admin(user)
    )


@router.websocket("/health")
async def health_socket(websocket: WebSocket, current_user: CurrentUserWS) -> None:
    """WebSocket health check endpoint.

    Requires an authenticated user (see ``get_current_user_ws``). Bounded
    per user and per worker, closed after `WS_MAX_LIFETIME_SECONDS`, and
    closed with 1009 on a message over `WS_MAX_MESSAGE_BYTES`.

    Args:
        websocket: Incoming WebSocket connection
        current_user: Authenticated user, validated before accept
    """
    if await _refuse_if_full(websocket, current_user):
        return

    client_id = str(id(websocket))
    deadline = time.monotonic() + settings.WS_MAX_LIFETIME_SECONDS
    try:
        await websocket.accept()
        _active_connections.setdefault("health", set()).add(websocket)
        logger.info("ws_health_connected", client_id=client_id)

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
                return

            await websocket.send_json(
                {"type": "ping", "timestamp": asyncio.get_event_loop().time()}
            )
            try:
                message = await asyncio.wait_for(
                    websocket.receive(), timeout=min(_HEALTH_IDLE_SECONDS, remaining)
                )
            except TimeoutError:
                if time.monotonic() >= deadline:
                    await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
                    return
                await websocket.send_json({"type": "timeout", "ok": False})
                await websocket.close()
                return

            if message["type"] == "websocket.disconnect":
                logger.info("ws_health_disconnected", client_id=client_id)
                return

            text = message.get("text")
            payload = text.encode() if text is not None else message.get("bytes") or b""
            if len(payload) > settings.WS_MAX_MESSAGE_BYTES:
                logger.warning("ws_health_message_too_big", client_id=client_id)
                await websocket.close(code=status.WS_1009_MESSAGE_TOO_BIG)
                return
            if text == "pong":
                await websocket.send_json({"type": "pong", "ok": True})
    except WebSocketDisconnect:
        logger.info("ws_health_disconnected", client_id=client_id)
    finally:
        _active_connections.setdefault("health", set()).discard(websocket)
        connection_limiter.release(current_user.id)


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
