"""Tests for `PaymentEventRepository`'s dedup-insert-first mechanism."""

from __future__ import annotations

import pytest

from app.repositories.payment_event_repository import PaymentEventRepository


class TestInsertIfNew:
    @pytest.fixture
    def anyio_backend(self) -> str:
        return "asyncio"

    async def test_first_insert_returns_the_event(self, session) -> None:
        repository = PaymentEventRepository(session)
        event = await repository.insert_if_new(
            provider="stripe",
            provider_event_id="evt_123",
            event_type="checkout.session.completed",
            payload={"id": "evt_123"},
        )
        assert event is not None
        assert event.provider_event_id == "evt_123"

    async def test_replayed_event_returns_none(self, session) -> None:
        repository = PaymentEventRepository(session)
        first = await repository.insert_if_new(
            provider="stripe",
            provider_event_id="evt_456",
            event_type="invoice.paid",
            payload={"id": "evt_456"},
        )
        await session.commit()
        assert first is not None

        replay = await repository.insert_if_new(
            provider="stripe",
            provider_event_id="evt_456",
            event_type="invoice.paid",
            payload={"id": "evt_456"},
        )
        assert replay is None

    async def test_mark_processed_sets_status_and_timestamp(self, session) -> None:
        repository = PaymentEventRepository(session)
        event = await repository.insert_if_new(
            provider="stripe",
            provider_event_id="evt_789",
            event_type="customer.subscription.updated",
            payload={},
        )
        assert event is not None

        await repository.mark_processed(event.id)
        refreshed = await repository.get(event.id)
        assert refreshed is not None
        assert refreshed.status == "processed"
        assert refreshed.processed_at is not None
