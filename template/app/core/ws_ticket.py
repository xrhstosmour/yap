"""Short-lived, single-use tickets for WebSocket authentication.

WebSocket handshakes cannot carry an `Authorization` header, so the client's
JWT access token would otherwise have to travel as a `?token=` query
parameter, which leaks into server/proxy access logs and browser history.
Instead, an authenticated client mints a ticket via `POST /auth/ws-ticket`
and connects with that; the ticket is opaque, single-use, and expires in
seconds, bounding the blast radius of a logged query string.
"""

from __future__ import annotations

import secrets
from typing import cast

from app.core.cache import get_redis

WS_TICKET_PREFIX = "ws_ticket:"
WS_TICKET_TTL_SECONDS = 30


async def create_ws_ticket(user_id: str) -> str:
    """Mint a single-use ticket bound to `user_id`.

    Args:
        user_id: ID of the already-authenticated user requesting the ticket.

    Returns:
        Opaque ticket string, valid for `WS_TICKET_TTL_SECONDS`.
    """
    ticket = secrets.token_urlsafe(32)
    redis_client = await get_redis()
    await redis_client.setex(
        f"{WS_TICKET_PREFIX}{ticket}", WS_TICKET_TTL_SECONDS, user_id
    )
    return ticket


async def consume_ws_ticket(ticket: str) -> str | None:
    """Atomically fetch and invalidate a ticket.

    Args:
        ticket: Ticket string presented by the WebSocket client.

    Returns:
        The bound user ID, or None if the ticket is missing/expired/already used.
    """
    redis_client = await get_redis()
    return cast("str | None", await redis_client.getdel(f"{WS_TICKET_PREFIX}{ticket}"))
