"""Tests for the OutboxEvent model and Outbox publisher."""

from __future__ import annotations

import asyncio
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import delete
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

    @pytest.mark.anyio
    async def test_get_pending_does_not_claim_rows_locked_by_a_concurrent_call(
        self, engine: AsyncEngine
    ) -> None:
        """Two concurrent get_pending() calls must not claim the same rows.

        Uses two independent sessions (separate connections, real commits,
        both kept open across the gather) so the database itself resolves
        the race via `with_for_update(skip_locked=True)`, modeling an
        overlapping Celery beat tick or a second worker polling the same
        outbox table. Without atomic claiming, both calls could return, and
        `process_outbox` would dispatch, the same events twice.

        Args:
            engine: Async engine fixture, used to open independent sessions.

        Returns:
            None.
        """
        event_type = "concurrent.claim.test"

        async with AsyncSession(engine, expire_on_commit=False) as setup_session:
            outbox = Outbox(setup_session)
            for i in range(4):
                await outbox.publish(event_type, {"index": i})
            await setup_session.commit()

        session_a = AsyncSession(engine, expire_on_commit=False)
        session_b = AsyncSession(engine, expire_on_commit=False)

        async def _claim(claim_session: AsyncSession) -> list[str]:
            events = await Outbox(claim_session).get_pending(limit=2)
            return [str(event.id) for event in events]

        try:
            # Both sessions stay open (locks held) for the full gather, so
            # the race is genuine regardless of exact task scheduling.
            first_ids, second_ids = await asyncio.gather(
                _claim(session_a), _claim(session_b)
            )

            assert first_ids, "first claim should have locked at least one row"
            assert second_ids, "second claim should have locked at least one row"
            assert set(first_ids).isdisjoint(second_ids)
        finally:
            await session_a.rollback()
            await session_b.rollback()
            await session_a.close()
            await session_b.close()

            async with AsyncSession(engine, expire_on_commit=False) as cleanup_session:
                await cleanup_session.execute(
                    delete(OutboxEvent).where(OutboxEvent.event_type == event_type)
                )
                await cleanup_session.commit()


class TestDeliveryGuarantee:
    """Pins the guarantee the outbox actually provides.

    The module documented itself as publishing "exactly-once". It does not,
    and cannot: the dispatcher publishes to the broker and only afterwards
    commits the row saying it did, so anything that interrupts the two leaves
    the event pending and it goes out again. These tests make the real
    guarantee, at-least-once, visible rather than leaving a consumer author
    to discover it from a duplicate in production.
    """

    @pytest.mark.anyio
    async def test_an_uncommitted_dispatch_is_published_again(
        self, engine: AsyncEngine
    ) -> None:
        """The duplicate window is broker publish until commit.

        Args:
            engine: Async engine fixture, used to open independent sessions.

        Returns:
            None.
        """
        event_type = "at.least.once.single"

        async with AsyncSession(engine, expire_on_commit=False) as setup_session:
            await Outbox(setup_session).publish(event_type, {"index": 0})
            await setup_session.commit()

        try:
            # A dispatch that reached the broker, marked the row in memory,
            # and then lost its worker before committing.
            async with AsyncSession(engine, expire_on_commit=False) as first:
                outbox = Outbox(first)
                events = await outbox.get_pending(limit=10)
                claimed = [e for e in events if e.event_type == event_type]
                assert len(claimed) == 1
                claimed_id = claimed[0].id
                await outbox.mark_published(claimed[0])
                await first.rollback()

            async with AsyncSession(engine, expire_on_commit=False) as second:
                events = await Outbox(second).get_pending(limit=10)
                republished = [e.id for e in events if e.event_type == event_type]
                await second.rollback()

            assert republished == [claimed_id]
        finally:
            async with AsyncSession(engine, expire_on_commit=False) as cleanup:
                await cleanup.execute(
                    delete(OutboxEvent).where(OutboxEvent.event_type == event_type)
                )
                await cleanup.commit()

    @pytest.mark.anyio
    async def test_a_crash_mid_batch_republishes_the_whole_batch(
        self, engine: AsyncEngine
    ) -> None:
        """The dispatcher commits per batch, not per event.

        Committing per event would release the `FOR UPDATE SKIP LOCKED`
        claim on everything not yet handled, so the batch commit is the
        right call. The cost is that the replay unit is the batch.

        Args:
            engine: Async engine fixture, used to open independent sessions.

        Returns:
            None.
        """
        event_type = "at.least.once.batch"

        async with AsyncSession(engine, expire_on_commit=False) as setup_session:
            outbox = Outbox(setup_session)
            for index in range(3):
                await outbox.publish(event_type, {"index": index})
            await setup_session.commit()

        try:
            async with AsyncSession(engine, expire_on_commit=False) as first:
                outbox = Outbox(first)
                events = await outbox.get_pending(limit=10)
                claimed = [e for e in events if e.event_type == event_type]
                assert len(claimed) == 3
                claimed_ids = [event.id for event in claimed]
                # Two published, then the worker dies on the third.
                await outbox.mark_published(claimed[0])
                await outbox.mark_published(claimed[1])
                await first.rollback()

            async with AsyncSession(engine, expire_on_commit=False) as second:
                events = await Outbox(second).get_pending(limit=10)
                republished = [e.id for e in events if e.event_type == event_type]
                await second.rollback()

            assert sorted(republished) == sorted(claimed_ids)
        finally:
            async with AsyncSession(engine, expire_on_commit=False) as cleanup:
                await cleanup.execute(
                    delete(OutboxEvent).where(OutboxEvent.event_type == event_type)
                )
                await cleanup.commit()

    def test_the_module_does_not_promise_exactly_once(self) -> None:
        """A wrong guarantee in a docstring is a bug in the consumer's code."""
        import app.models.outbox as module

        documentation = (module.__doc__ or "").lower()

        assert "at-least-once" in documentation
        assert "idempotent" in documentation
