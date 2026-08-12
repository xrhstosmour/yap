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
from sqlalchemy import Index
from sqlmodel import Field
from sqlmodel import select

from app.core.tenant import get_current_tenant_id
from app.models.base import BaseModel

# Distinguishes "caller did not pass tenant_id" (use the current tenant
# context) from "caller explicitly passed tenant_id=None" (a genuinely
# system-wide event with no tenant owner), which a plain `= None` default
# cannot tell apart.
_UNSET: UUID | None = object()  # type: ignore[assignment]


class OutboxEvent(BaseModel, table=True):
    """Outbox event awaiting publication.

    Written in the same DB transaction as business operations.
    Published to the message broker by a background dispatcher.
    """

    __tablename__ = "outbox_events"  # pyright: ignore[reportAssignmentType]
    # Compound index on (status, created_at) eliminates the sort pass in
    # get_pending(), which queries WHERE status = 'pending' ORDER BY created_at ASC.
    __table_args__ = (
        Index("ix_outbox_events_status_created_at", "status", "created_at"),
    )

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

    # Compound index (status, created_at) covers single-column status lookups.
    status: str = Field(
        default="pending",
        index=False,
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
        tenant_id: UUID | None = _UNSET,
    ) -> OutboxEvent:
        """Write an event to the outbox within the current transaction.

        Args:
            event_type: Dotted event name (e.g. "user.created").
            payload: Event payload, published as-is.
            tenant_id: Owning tenant. Defaults to the current tenant context;
                pass `tenant_id=None` explicitly for a genuinely system-wide
                event with no single tenant owner.

        Returns:
            The pending `OutboxEvent`, already added to the session.
        """
        if tenant_id is _UNSET:
            tenant_id = get_current_tenant_id()
        event = OutboxEvent(
            id=uuid7(),
            event_type=event_type,
            payload=payload,
            status="pending",
            tenant_id=tenant_id,
        )
        self.session.add(event)
        return event

    async def mark_published(self, event: OutboxEvent) -> None:
        """Mark an already-fetched event as published.

        Args:
            event: The OutboxEvent instance to update, as returned by
                `get_pending()`. Updating the object directly avoids a
                redundant re-`SELECT` by primary key.
        """
        event.status = "published"
        event.published_at = datetime.now(UTC)

    async def mark_failed(self, event: OutboxEvent) -> None:
        """Mark an already-fetched event as failed, retrying or dead-lettering.

        Args:
            event: The OutboxEvent instance to update, as returned by
                `get_pending()`. Updating the object directly avoids a
                redundant re-`SELECT` by primary key.
        """
        event.retry_count += 1
        if event.retry_count >= 5:
            event.status = "dead"
        else:
            event.status = "pending"

    async def get_pending(self, limit: int = 100) -> list[OutboxEvent]:
        result = await self.session.execute(
            select(OutboxEvent)
            .where(OutboxEvent.status == "pending")  # type: ignore[arg-type]
            .order_by(OutboxEvent.created_at.asc())  # type: ignore[attr-defined]
            .limit(limit)
        )
        return list(result.scalars().all())
