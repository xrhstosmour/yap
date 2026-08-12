"""Tests for the OutboxEvent model and Outbox publisher."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.tenant import tenant_context
from app.models.outbox import Outbox
from app.models.outbox import OutboxEvent


class TestOutbox:
    """Tests for Outbox event publishing and lifecycle management."""

    @pytest.mark.anyio
    async def test_publish_creates_pending_event(self, session: AsyncSession) -> None:
        """Publishing an event creates a pending OutboxEvent record."""
        outbox = Outbox(session)
        event = await outbox.publish(
            event_type="user.created",
            payload={"user_id": "abc-123"},
        )

        assert isinstance(event, OutboxEvent)
        assert event.id is not None
        assert event.event_type == "user.created"
        assert event.payload == {"user_id": "abc-123"}
        assert event.status == "pending"
        assert event.retry_count == 0
        assert event.published_at is None

    @pytest.mark.anyio
    async def test_get_pending_returns_only_pending_events(
        self, session: AsyncSession
    ) -> None:
        """get_pending returns only events with status 'pending'."""
        outbox = Outbox(session)

        pending = await outbox.publish("order.created", {"order_id": "1"})
        published = await outbox.publish("order.created", {"order_id": "2"})
        await session.flush()
        await outbox.mark_published(published)
        await session.flush()

        results = await outbox.get_pending()

        statuses = {e.status for e in results}
        assert "published" not in statuses
        assert pending.id in {e.id for e in results}

    @pytest.mark.anyio
    async def test_mark_published_updates_status(self, session: AsyncSession) -> None:
        """mark_published sets status to 'published' and sets published_at."""
        outbox = Outbox(session)
        event = await outbox.publish("user.created", {"user_id": "x"})
        await session.flush()

        await outbox.mark_published(event)

        result = await session.execute(
            select(OutboxEvent).where(OutboxEvent.id == event.id)
        )
        updated = result.scalar_one_or_none()
        assert updated is not None
        assert updated.status == "published"
        assert updated.published_at is not None

    @pytest.mark.anyio
    async def test_mark_failed_increments_retry_and_resets_status(
        self, session: AsyncSession
    ) -> None:
        """mark_failed increments retry_count and resets status to 'pending'."""
        outbox = Outbox(session)
        event = await outbox.publish("order.created", {"order_id": "1"})
        await session.flush()

        await outbox.mark_failed(event)

        result = await session.execute(
            select(OutboxEvent).where(OutboxEvent.id == event.id)
        )
        updated = result.scalar_one_or_none()
        assert updated is not None
        assert updated.retry_count == 1
        assert updated.status == "pending"

    @pytest.mark.anyio
    async def test_mark_published_does_not_query_the_database(
        self, session: AsyncSession
    ) -> None:
        """mark_published updates the passed event without a re-SELECT."""
        outbox = Outbox(session)
        event = await outbox.publish("user.created", {"user_id": "x"})
        await session.flush()

        with patch.object(session, "execute", wraps=session.execute) as execute_spy:
            await outbox.mark_published(event)

        execute_spy.assert_not_called()

    @pytest.mark.anyio
    async def test_mark_failed_does_not_query_the_database(
        self, session: AsyncSession
    ) -> None:
        """mark_failed updates the passed event without a re-SELECT."""
        outbox = Outbox(session)
        event = await outbox.publish("order.created", {"order_id": "1"})
        await session.flush()

        with patch.object(session, "execute", wraps=session.execute) as execute_spy:
            await outbox.mark_failed(event)

        execute_spy.assert_not_called()

    @pytest.mark.anyio
    async def test_mark_failed_moves_to_dead_after_max_retries(
        self, session: AsyncSession
    ) -> None:
        """mark_failed sets status to 'dead' when retry_count reaches 5."""
        outbox = Outbox(session)
        event = await outbox.publish("order.created", {"order_id": "1"})
        event.retry_count = 4
        await session.flush()

        await outbox.mark_failed(event)

        result = await session.execute(
            select(OutboxEvent).where(OutboxEvent.id == event.id)
        )
        updated = result.scalar_one_or_none()
        assert updated is not None
        assert updated.retry_count == 5
        assert updated.status == "dead"

    @pytest.mark.anyio
    async def test_publish_defaults_tenant_id_from_current_context(
        self, session: AsyncSession
    ) -> None:
        """publish() should pick up the ambient tenant when none is passed."""
        outbox = Outbox(session)
        tenant_id = uuid4()

        with tenant_context(tenant_id):
            event = await outbox.publish("user.created", {"user_id": "abc-123"})

        assert event.tenant_id == tenant_id

    @pytest.mark.anyio
    async def test_publish_explicit_none_overrides_context(
        self, session: AsyncSession
    ) -> None:
        """An explicit tenant_id=None must stay None, not fall back to context."""
        outbox = Outbox(session)

        with tenant_context(uuid4()):
            event = await outbox.publish(
                "system.maintenance", {"reason": "backup"}, tenant_id=None
            )

        assert event.tenant_id is None

    @pytest.mark.anyio
    async def test_get_pending_respects_limit(self, session: AsyncSession) -> None:
        """get_pending returns at most `limit` events."""
        outbox = Outbox(session)
        for i in range(5):
            await outbox.publish(f"event.{i}", {"index": i})
        await session.flush()

        results = await outbox.get_pending(limit=3)

        assert len(results) == 3
