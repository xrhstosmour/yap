"""Tests for WebSocket auth ticket minting/consumption.

Tests cover:
- create_ws_ticket() stores the user ID under a namespaced key with TTL
- consume_ws_ticket() reads via an atomic GETDEL and forwards the ticket
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest

from app.core.ws_ticket import WS_TICKET_PREFIX
from app.core.ws_ticket import WS_TICKET_TTL_SECONDS
from app.core.ws_ticket import consume_ws_ticket
from app.core.ws_ticket import create_ws_ticket


@pytest.fixture
def mock_redis_client() -> AsyncMock:
    """Return an AsyncMock that mimics a redis.asyncio.Redis client."""
    redis = AsyncMock()
    redis.setex = AsyncMock()
    redis.getdel = AsyncMock()
    return redis


@pytest.fixture
def _patch_get_redis(mock_redis_client: AsyncMock) -> None:
    """Patch get_redis() to return the mocked Redis client for every test."""
    with patch(
        "app.core.ws_ticket.get_redis",
        AsyncMock(return_value=mock_redis_client),
    ):
        yield


@pytest.mark.asyncio
async def test_create_ws_ticket_stores_user_id_with_ttl(
    mock_redis_client: AsyncMock,
    _patch_get_redis: None,
) -> None:
    """create_ws_ticket() should SETEX the user ID under a namespaced key."""
    ticket = await create_ws_ticket("user-123")

    mock_redis_client.setex.assert_awaited_once()
    key_arg, ttl_arg, value_arg = mock_redis_client.setex.call_args.args
    assert key_arg == f"{WS_TICKET_PREFIX}{ticket}"
    assert ttl_arg == WS_TICKET_TTL_SECONDS
    assert value_arg == "user-123"


@pytest.mark.asyncio
async def test_create_ws_ticket_returns_unique_tickets(
    mock_redis_client: AsyncMock,
    _patch_get_redis: None,
) -> None:
    """Successive calls should mint distinct, unguessable tickets."""
    first = await create_ws_ticket("user-1")
    second = await create_ws_ticket("user-1")

    assert first != second


@pytest.mark.asyncio
async def test_consume_ws_ticket_forwards_namespaced_key(
    mock_redis_client: AsyncMock,
    _patch_get_redis: None,
) -> None:
    """consume_ws_ticket() should GETDEL the namespaced key and return its value."""
    mock_redis_client.getdel.return_value = "user-456"

    user_id = await consume_ws_ticket("abc")

    mock_redis_client.getdel.assert_awaited_once_with(f"{WS_TICKET_PREFIX}abc")
    assert user_id == "user-456"


@pytest.mark.asyncio
async def test_consume_ws_ticket_returns_none_when_missing(
    mock_redis_client: AsyncMock,
    _patch_get_redis: None,
) -> None:
    """A missing/expired/already-used ticket should resolve to None."""
    mock_redis_client.getdel.return_value = None

    user_id = await consume_ws_ticket("unknown")

    assert user_id is None
