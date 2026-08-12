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
