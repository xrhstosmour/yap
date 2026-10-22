"""Tests for the process_outbox() Celery task."""

from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import uuid4

# See tests/tasks/test_cleanup.py for why this stub eviction is needed.
if isinstance(sys.modules.get("app.tasks.outbox"), MagicMock):
    del sys.modules["app.tasks.outbox"]

from app.models.outbox import OutboxEvent  # noqa: E402
from app.tasks.outbox import BATCH_SIZE  # noqa: E402
from app.tasks.outbox import process_outbox  # noqa: E402


def _make_event(tenant_id: Any = None) -> OutboxEvent:
    event = MagicMock(spec=OutboxEvent)
    event.id = uuid4()
    event.event_type = "user.created"
    event.payload = {"user_id": "abc-123"}
    event.tenant_id = tenant_id
    return event


def test_process_outbox_forwards_tenant_id_in_envelope() -> None:
    """The dispatched task payload should carry tenant_id alongside payload."""
    tenant_id = uuid4()
    event = _make_event(tenant_id=tenant_id)

    mock_session = AsyncMock()
    mock_outbox = AsyncMock()
    mock_outbox.get_pending = AsyncMock(return_value=[event])

    @asynccontextmanager
    async def _factory() -> Any:
        yield mock_session

    with (
        patch("app.database.celery_session_factory", _factory),
        patch("app.models.outbox.Outbox", return_value=mock_outbox),
        patch("app.tasks.outbox.celery_app.send_task") as mock_send_task,
    ):
        result = process_outbox.apply()

    assert result.successful()
    assert result.result["processed"] == 1
    mock_send_task.assert_called_once()
    _, kwargs = mock_send_task.call_args
    envelope = json.loads(kwargs["args"][0])
    assert envelope["tenant_id"] == str(tenant_id)
    assert envelope["payload"] == {"user_id": "abc-123"}


def test_process_outbox_identifies_the_event_in_the_envelope() -> None:
    """A consumer has to be able to recognise a redelivery.

    Delivery is at-least-once, so anything a consumer writes durably needs
    a key to be idempotent against, and the payload rarely carries one.
    """
    event = _make_event()

    mock_session = AsyncMock()
    mock_outbox = AsyncMock()
    mock_outbox.get_pending = AsyncMock(return_value=[event])

    @asynccontextmanager
    async def _factory() -> Any:
        yield mock_session

    with (
        patch("app.database.celery_session_factory", _factory),
        patch("app.models.outbox.Outbox", return_value=mock_outbox),
        patch("app.tasks.outbox.celery_app.send_task") as mock_send_task,
    ):
        result = process_outbox.apply()

    assert result.successful()
    _, kwargs = mock_send_task.call_args
    envelope = json.loads(kwargs["args"][0])
    assert envelope["event_id"] == str(event.id)
    # The type as well, so a consumer subscribed to several can tell them
    # apart without inferring it from the payload's shape.
    assert envelope["event_type"] == event.event_type


def test_process_outbox_forwards_null_tenant_id() -> None:
    """A system-wide event (no tenant) should serialise tenant_id as null."""
    event = _make_event(tenant_id=None)

    mock_session = AsyncMock()
    mock_outbox = AsyncMock()
    mock_outbox.get_pending = AsyncMock(return_value=[event])

    @asynccontextmanager
    async def _factory() -> Any:
        yield mock_session

    with (
        patch("app.database.celery_session_factory", _factory),
        patch("app.models.outbox.Outbox", return_value=mock_outbox),
        patch("app.tasks.outbox.celery_app.send_task") as mock_send_task,
    ):
        result = process_outbox.apply()

    assert result.successful()
    _, kwargs = mock_send_task.call_args
    envelope = json.loads(kwargs["args"][0])
    assert envelope["tenant_id"] is None


def _drain(batches: list[list[OutboxEvent]], send_task: Any = None) -> Any:
    """Run the task against `get_pending` returning these batches in turn.

    Args:
        batches: What each successive `get_pending` call returns.
        send_task: Replacement for `send_task`, a plain mock by default.

    Returns:
        The task result and the mocked outbox and session.
    """
    mock_session = AsyncMock()
    mock_outbox = AsyncMock()
    mock_outbox.get_pending = AsyncMock(side_effect=batches)

    @asynccontextmanager
    async def _factory() -> Any:
        yield mock_session

    with (
        patch("app.database.celery_session_factory", _factory),
        patch("app.models.outbox.Outbox", return_value=mock_outbox),
        patch("app.tasks.outbox.celery_app.send_task", send_task or MagicMock()),
    ):
        result = process_outbox.apply()
    return result, mock_outbox, mock_session


def test_a_backlog_is_drained_in_one_run() -> None:
    """One batch per beat tick capped dispatch at ten events a second."""
    full = [_make_event() for _ in range(BATCH_SIZE)]
    result, mock_outbox, mock_session = _drain([full, full, [_make_event()]])

    assert result.result["processed"] == 2 * BATCH_SIZE + 1
    assert mock_outbox.get_pending.await_count == 3
    # Each batch commits, so its claim on the rows ends with it.
    assert mock_session.commit.await_count == 3


def test_a_failing_batch_stops_the_drain() -> None:
    """Refetching at once would spend every retry in a broker outage."""
    full = [_make_event() for _ in range(BATCH_SIZE)]
    result, mock_outbox, _ = _drain(
        [full, full], send_task=MagicMock(side_effect=ConnectionError("broker down"))
    )

    assert result.result["failed"] == BATCH_SIZE
    assert mock_outbox.get_pending.await_count == 1


def test_the_drain_stops_at_its_deadline() -> None:
    """A run hands over to the next beat tick instead of overlapping it."""
    full = [_make_event() for _ in range(BATCH_SIZE)]
    with patch("app.tasks.outbox.DRAIN_SECONDS", 0.0):
        result, mock_outbox, _ = _drain([full, full])

    assert result.result["processed"] == BATCH_SIZE
    assert mock_outbox.get_pending.await_count == 1
