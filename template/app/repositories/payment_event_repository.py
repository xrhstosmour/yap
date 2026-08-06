"""PaymentEvent repository: the webhook dedup-insert-first mechanism."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import Any
from typing import cast
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.logging import get_logger
from app.models.payment_event import PaymentEvent
from app.models.payment_event import PaymentEventStatus
from app.repositories.base import BaseRepository

logger = get_logger("repository.payment_event")


class PaymentEventRepository(BaseRepository[PaymentEvent]):
    """Repository for `PaymentEvent` model operations."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, PaymentEvent)

    async def insert_if_new(
        self,
        provider: str,
        provider_event_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> PaymentEvent | None:
        """Dedup-insert-first: insert the event, or detect a replay.

        `INSERT ... ON CONFLICT (provider, provider_event_id) DO UPDATE
        ... WHERE status = 'failed'`. Returns the `PaymentEvent` row
        when it's either newly inserted or a retry of a previously
        *failed* delivery (reset back to `received` so the caller
        reprocesses it) — or `None` if a row already exists in
        `received`/`processed` status (a true replay of an event
        already handled or currently in flight), in which case the
        caller should return `200` immediately without reprocessing.

        Plain `DO NOTHING` would have made this dedup check permanently
        swallow every Stripe retry of an event that failed once (e.g. a
        transient DB error) — Stripe retries non-2xx responses for up
        to several days, but the retried delivery would forever match
        the existing `failed` row and never actually reprocess.
        """
        statement = (
            pg_insert(PaymentEvent)
            .values(
                provider=provider,
                provider_event_id=provider_event_id,
                event_type=event_type,
                payload=payload,
                status=PaymentEventStatus.RECEIVED,
            )
            .on_conflict_do_update(
                index_elements=["provider", "provider_event_id"],
                set_={
                    "status": PaymentEventStatus.RECEIVED,
                    "payload": payload,
                    "processed_at": None,
                },
                where=(cast(Any, PaymentEvent.status) == PaymentEventStatus.FAILED),
            )
            .returning(PaymentEvent.id)  # type: ignore[call-overload]
        )
        result = await self.session.execute(statement)
        inserted_id = result.scalar_one_or_none()
        if inserted_id is None:
            return None

        await self.session.flush()
        return await self.get(inserted_id)

    async def mark_processed(
        self, event_id: UUID, tenant_id: UUID | None = None
    ) -> None:
        data: dict[str, Any] = {
            "status": PaymentEventStatus.PROCESSED,
            "processed_at": datetime.now(UTC),
        }
        if tenant_id is not None:
            data["tenant_id"] = tenant_id
        await self.update(event_id, data)

    async def mark_failed(self, event_id: UUID) -> None:
        await self.update(
            event_id,
            {
                "status": PaymentEventStatus.FAILED,
                "processed_at": datetime.now(UTC),
            },
        )

    async def get_by_provider_event_id(
        self, provider: str, provider_event_id: str
    ) -> PaymentEvent | None:
        query = select(PaymentEvent).where(
            PaymentEvent.provider == provider,  # type: ignore[arg-type]
            PaymentEvent.provider_event_id == provider_event_id,  # type: ignore[arg-type]
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
