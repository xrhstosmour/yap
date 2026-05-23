"""Outbox pattern for reliable event publishing.

Ensures events are published exactly-once by writing them to the database
in the same transaction as business operations. A background process
publishes pending events to the message broker (RabbitMQ/Redis).

This prevents the dual-write problem where a database write succeeds
but the event publish fails.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import Any
from uuid import UUID
from uuid import uuid7

from sqlalchemy import JSON
from sqlmodel import Field

from app.models.base import BaseModel


class OutboxEvent(BaseModel, table=True):
    """Outbox event awaiting publication.

    Written in the same DB transaction as business operations.
    Published to the message broker by a background dispatcher.
    """

    __tablename__ = "outbox_events"  # pyright: ignore[reportAssignmentType]

    event_type: str = Field(
        nullable=False,
        index=True,
        max_length=200,
    )

    payload: dict[str, Any] = Field(
        default_factory=dict,
        sa_type=JSON,
        nullable=False,
    )

    status: str = Field(
        default="pending",
        index=True,
        max_length=20,
    )

    published_at: datetime | None = Field(default=None)

    retry_count: int = Field(default=0)

    tenant_id: UUID | None = Field(
        default=None,
        index=True,
    )


class Outbox:
    """Outbox publisher for transactional event publishing.

    Events are written to the outbox table within the current transaction.
    They are dispatched asynchronously by the outbox_processor task.

    Usage::

        outbox = Outbox(session)
        await outbox.publish(
            event_type="user.created",
            payload={"user_id": str(user.id)},
        )
    """

    def __init__(self, session) -> None:
        self.session = session

    async def publish(
        self,
        event_type: str,
        payload: dict[str, Any],
        tenant_id: UUID | None = None,
    ) -> OutboxEvent:
        event = OutboxEvent(
            id=uuid7(),
            event_type=event_type,
            payload=payload,
            status="pending",
            tenant_id=tenant_id,
        )
        self.session.add(event)
        return event

    async def mark_published(self, event_id: UUID) -> None:
        from sqlalchemy import select as sa_select

        result = await self.session.execute(
            sa_select(OutboxEvent).where(OutboxEvent.id == event_id)  # type: ignore[arg-type]
        )
        event = result.scalar_one_or_none()
        if event:
            event.status = "published"
            event.published_at = datetime.now(UTC)

    async def mark_failed(self, event_id: UUID) -> None:
        from sqlalchemy import select as sa_select

        result = await self.session.execute(
            sa_select(OutboxEvent).where(OutboxEvent.id == event_id)  # type: ignore[arg-type]
        )
        event = result.scalar_one_or_none()
        if event:
            event.retry_count += 1
            if event.retry_count >= 5:
                event.status = "dead"
            else:
                event.status = "pending"

    async def get_pending(self, limit: int = 100) -> list[OutboxEvent]:
        from sqlalchemy import select as sa_select

        result = await self.session.execute(
            sa_select(OutboxEvent)
            .where(OutboxEvent.status == "pending")  # type: ignore[arg-type]
            .order_by(OutboxEvent.created_at.asc())  # type: ignore[attr-defined]
            .limit(limit)
        )
        return list(result.scalars().all())
