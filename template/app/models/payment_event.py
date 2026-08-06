"""PaymentEvent model: raw webhook audit/dedup log.

`PaymentEvent` is not tenant-scoped at write time — a webhook arrives
before any tenant is resolvable. It is a raw webhook audit/dedup log,
not a payment ledger: it exists to make webhook processing safe (via
the `(provider, provider_event_id)` unique index, the actual dedup
mechanism), not to answer "what did this tenant pay and when." That's
`Payment` (`app.models.payment`).
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import JSON
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Index
from sqlmodel import Field

from app.models.base import BaseModel


class PaymentEventStatus(enum.StrEnum):
    """Processing status of a raw webhook event."""

    RECEIVED = "received"
    PROCESSED = "processed"
    FAILED = "failed"


class PaymentEvent(BaseModel, table=True):
    """A raw payment-provider webhook event, deduplicated by provider event ID.

    Attributes:
        id: UUID primary key
        provider: `"stripe"` — kept as a column now purely so no
            migration is needed when a second provider is added later
        provider_event_id: The provider's event ID (Stripe's `event.id`)
        event_type: The provider's event type string
        payload: Raw event JSON. Never logged above `debug`, scrub PII
            even then.
        status: received / processed / failed, mirrors `OutboxEvent`
            status naming
        processed_at: When processing completed (success or failure)
        tenant_id: Populated once resolved, for debugging only — never
            used for access control
    """

    __tablename__ = "payment_events"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        Index(
            "ix_payment_events_provider_provider_event_id",
            "provider",
            "provider_event_id",
            unique=True,
        ),
    )

    provider: str = Field(default="stripe", nullable=False, max_length=32)

    provider_event_id: str = Field(nullable=False, max_length=255, index=False)

    event_type: str = Field(nullable=False, index=True, max_length=200)

    payload: dict[str, Any] = Field(
        default_factory=dict,
        sa_type=JSON,
        nullable=False,
    )

    status: PaymentEventStatus = Field(
        default=PaymentEventStatus.RECEIVED,
        nullable=False,
        sa_type=SAEnum(
            PaymentEventStatus,
            values_callable=lambda e: [m.value for m in e],
        ),  # type: ignore[call-overload]
    )

    processed_at: datetime | None = Field(default=None)
