"""Payment models: the payment-transaction ledger and saved payment methods.

`Payment` is the actual payment-transaction ledger/history — one row
per charge attempt, success or failure, independent of which `Invoice`
it settles. It is populated *exclusively* from webhook handlers, never
from a client-facing endpoint, since payment state must only ever
reflect what Stripe confirms actually happened.

`PaymentMethod` is reference-only metadata for a tenant's saved payment
method (e.g. "Visa ending in 4242") so a billing page can render
without a live Stripe API call on every load. It never stores raw card
data, only what Stripe's API returns as safe-to-store metadata.

Both are tenant-scoped in the normal way. Contrast with `PaymentEvent`
(`app.models.payment_event`), which is not tenant-scoped at write time
since it arrives before a tenant is resolvable.
"""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Index
from sqlmodel import Field

from app.models.base import BaseModel


class PaymentMethodType(enum.StrEnum):
    """Kind of payment method stored for a tenant."""

    CARD = "card"
    SEPA_DEBIT = "sepa_debit"


class PaymentStatus(enum.StrEnum):
    """Status of a single payment/charge attempt."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class PaymentMethod(BaseModel, table=True):
    """Reference-only metadata for a tenant's saved payment method.

    Synced from `customer.updated`/`payment_method.attached`/
    `payment_method.detached` webhooks and the Billing Portal return
    flow — never entered directly by a client.

    Attributes:
        id: UUID primary key
        stripe_payment_method_id: Stripe `PaymentMethod` object ID, unique
        type: card / sepa_debit
        brand: Card brand (e.g. "visa"), nullable
        last_four: Last four digits
        exp_month: Card expiry month, card-only
        exp_year: Card expiry year, card-only
        is_default: Whether this is the tenant's default payment method
        tenant_id: The owning tenant
    """

    __tablename__ = "payment_methods"  # pyright: ignore[reportAssignmentType]

    stripe_payment_method_id: str = Field(
        unique=True, index=True, nullable=False, max_length=255
    )

    type: PaymentMethodType = Field(
        nullable=False,
        sa_type=SAEnum(
            PaymentMethodType,
            values_callable=lambda e: [m.value for m in e],
        ),  # type: ignore[call-overload]
    )

    brand: str | None = Field(default=None, max_length=32)

    last_four: str | None = Field(default=None, max_length=4)

    exp_month: int | None = Field(default=None)

    exp_year: int | None = Field(default=None)

    is_default: bool = Field(default=False, nullable=False)


class Payment(BaseModel, table=True):
    """A single payment/charge attempt, success or failure.

    Populated exclusively from webhook handlers (`invoice.paid` ->
    `succeeded`, `invoice.payment_failed` -> `failed`, `charge.refunded`
    -> `refunded`/`partially_refunded`).

    Attributes:
        id: UUID primary key
        invoice_id: FK to `Invoice`, nullable — a *failed* payment
            attempt may have no invoice yet (an `Invoice` is only
            minted once the charge succeeds)
        subscription_id: FK to `Subscription`, nullable
        payment_method_id: FK to `PaymentMethod`, nullable
        provider: `"stripe"` — kept as a column now purely so no
            migration is needed when a second provider is added later
        stripe_payment_intent_id: Stripe `PaymentIntent` ID, nullable
        stripe_charge_id: Stripe `Charge` ID, nullable
        amount_cents: Amount charged
        currency: ISO 4217 currency code, lowercase
        status: pending / succeeded / failed / refunded / partially_refunded
        failure_code: Stripe's decline/error reason code, nullable
        failure_message: Stripe's decline/error reason message, nullable
        refunded_amount_cents: Amount refunded so far, default 0
        paid_at: When the payment succeeded
        refunded_at: When (any part of) the payment was refunded
        tenant_id: The paying tenant
    """

    __tablename__ = "payments"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        Index("ix_payments_tenant_id_created_at", "tenant_id", "created_at"),
        Index("ix_payments_subscription_id_status", "subscription_id", "status"),
    )

    invoice_id: UUID | None = Field(default=None, foreign_key="invoices.id")

    subscription_id: UUID | None = Field(
        default=None, foreign_key="subscriptions.id", index=False
    )

    payment_method_id: UUID | None = Field(
        default=None, foreign_key="payment_methods.id"
    )

    provider: str = Field(default="stripe", nullable=False, max_length=32)

    stripe_payment_intent_id: str | None = Field(default=None, max_length=255)

    stripe_charge_id: str | None = Field(default=None, max_length=255)

    amount_cents: int = Field(nullable=False)

    currency: str = Field(default="eur", nullable=False, max_length=3)

    status: PaymentStatus = Field(
        nullable=False,
        index=False,
        sa_type=SAEnum(
            PaymentStatus,
            values_callable=lambda e: [m.value for m in e],
        ),  # type: ignore[call-overload]
    )

    failure_code: str | None = Field(default=None, max_length=100)

    failure_message: str | None = Field(default=None, max_length=1000)

    refunded_amount_cents: int = Field(default=0, nullable=False)

    paid_at: datetime | None = Field(default=None)

    refunded_at: datetime | None = Field(default=None)
