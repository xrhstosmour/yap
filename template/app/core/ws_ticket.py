"""Short-lived, single-use tickets for WebSocket authentication.

WebSocket handshakes cannot carry an `Authorization` header, so the client's
JWT access token would otherwise have to travel as a `?token=` query
parameter, which leaks into server/proxy access logs and browser history.
Instead, an authenticated client mints a ticket via `POST /auth/ws-ticket`
and connects with that; the ticket is opaque, single-use, and expires in
seconds, bounding the blast radius of a logged query string.

A ticket also carries the user's `token_version` at minting time, so a
password change or admin revoke between minting and connecting is noticed by
comparing it with the user's current one. Only `/ws/metrics` compares again
while the socket is open, `/ws/health` does not.
"""

from __future__ import annotations

import secrets
from typing import NamedTuple
from typing import cast

from app.core.cache import get_redis

WS_TICKET_PREFIX = "ws_ticket:"
WS_TICKET_TTL_SECONDS = 30


class WebSocketTicket(NamedTuple):
    """Who a ticket was minted for, and which session generation."""

    user_id: str
    token_version: int


async def create_ws_ticket(user_id: str, token_version: int) -> str:
    """Mint a single-use ticket bound to `user_id`.

    Args:
        user_id: ID of the already-authenticated user requesting the ticket.
        token_version: The user's `token_version` when the ticket is minted.

    Returns:
        Opaque ticket string, valid for `WS_TICKET_TTL_SECONDS`.
    """
    ticket = secrets.token_urlsafe(32)
    redis_client = await get_redis()
    await redis_client.setex(
        f"{WS_TICKET_PREFIX}{ticket}",
        WS_TICKET_TTL_SECONDS,
        f"{user_id}:{token_version}",
    )
    return ticket


async def consume_ws_ticket(ticket: str) -> WebSocketTicket | None:
    """Atomically fetch and invalidate a ticket.

    Args:
        ticket: Ticket string presented by the WebSocket client.

    Returns:
        The bound user ID and `token_version`, or None if the ticket is
        missing, expired, already used or malformed.
    """
    redis_client = await get_redis()
    stored = cast(
        "str | None", await redis_client.getdel(f"{WS_TICKET_PREFIX}{ticket}")
    )
    if not stored:
        return None
    user_id, _, token_version = stored.rpartition(":")
    if not user_id or not token_version.isdigit():
        return None
    return WebSocketTicket(user_id, int(token_version))
