"""Invoice models: our own gapless, sequentially-numbered invoice record.

Stripe's invoice numbers do not satisfy Greek/EU compliance
requirements for sequential, gapless numbering — `Invoice` exists only
to guarantee that, and to capture the fields a future myDATA
submission would need. VAT rate/reverse-charge determination is
delegated entirely to Stripe Tax; nothing here calculates VAT or
validates VAT IDs.

`Invoice` and `InvoiceLineItem` are tenant-scoped in the normal way —
each invoice belongs to a specific paying tenant. `InvoiceSequence` is
the exception: Greek/EU gapless-numbering compliance applies to the
*issuer* (this SaaS business), not per customer, so it is a single
global counter per series (e.g. one counter for `"2026"` shared by every
tenant's invoices) rather than a per-tenant sequence. It therefore
follows the same `tenant_id`-always-`NULL` pattern as `Plan`/`Coupon`,
with the matching tenant-filter-bypass override on its repository.
"""

from __future__ import annotations

import enum
from datetime import date as date_type
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import JSON
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Numeric
from sqlmodel import Field

from app.models.base import BaseModel


class InvoiceStatus(enum.StrEnum):
    """Lifecycle status of an `Invoice`."""

    DRAFT = "draft"
    OPEN = "open"
    PAID = "paid"
    VOID = "void"
    UNCOLLECTIBLE = "uncollectible"


class Invoice(BaseModel, table=True):
    """A gapless, sequentially-numbered invoice for a tenant's payment.

    Attributes:
        id: UUID primary key
        subscription_id: FK to `Subscription`, nullable
        stripe_invoice_id: Stripe `Invoice` object ID
        invoice_number: Our own sequential number (e.g.
            `INV-2026-000123`), unique, minted from `InvoiceSequence`
        status: draft / open / paid / void / uncollectible
        issue_date: Date the invoice was issued
        amount_due_cents: Total amount due
        amount_paid_cents: Amount actually paid
        currency: ISO 4217 currency code, lowercase
        vat_rate: VAT rate applied, as determined by Stripe Tax
        vat_amount_cents: VAT amount, as determined by Stripe Tax
        vat_id: Customer's VAT/ΑΦΜ if provided
        customer_country: Customer's country, as determined by Stripe Tax
        reverse_charge: Whether reverse charge applies (B2B, VIES-validated
            by Stripe Tax)
        billing_name: Customer billing name, snapshotted at issue time —
            must not change retroactively
        billing_address: Customer billing address, snapshotted at issue
            time — must not change retroactively
        paid_at: When the invoice was paid
        hosted_invoice_url: Stripe-hosted invoice page URL
        tenant_id: The paying tenant
    """

    __tablename__ = "invoices"  # pyright: ignore[reportAssignmentType]

    subscription_id: UUID | None = Field(
        default=None, foreign_key="subscriptions.id", index=True
    )

    stripe_invoice_id: str | None = Field(
        default=None, max_length=255, unique=True, index=True
    )

    invoice_number: str = Field(unique=True, index=True, nullable=False, max_length=64)

    status: InvoiceStatus = Field(
        nullable=False,
        sa_type=SAEnum(
            InvoiceStatus,
            values_callable=lambda e: [m.value for m in e],
        ),  # type: ignore[call-overload]
    )

    issue_date: date_type = Field(nullable=False)

    amount_due_cents: int = Field(nullable=False)

    amount_paid_cents: int = Field(default=0, nullable=False)

    currency: str = Field(default="eur", nullable=False, max_length=3)

    vat_rate: Decimal = Field(
        default=Decimal("0"), sa_type=Numeric(5, 4), nullable=False
    )  # type: ignore[call-overload]

    vat_amount_cents: int = Field(default=0, nullable=False)

    vat_id: str | None = Field(default=None, max_length=64)

    customer_country: str | None = Field(default=None, max_length=2)

    reverse_charge: bool = Field(default=False, nullable=False)

    billing_name: str | None = Field(default=None, max_length=255)

    billing_address: dict[str, Any] = Field(
        default_factory=dict,
        sa_type=JSON,
        nullable=False,
    )

    paid_at: datetime | None = Field(default=None)

    hosted_invoice_url: str | None = Field(default=None, max_length=1024)


class InvoiceLineItem(BaseModel, table=True):
    """A single line item on an `Invoice`.

    Attributes:
        id: UUID primary key
        invoice_id: FK to the parent `Invoice`
        description: Human-readable line description
        quantity: Quantity billed
        unit_amount_cents: Price per unit
        amount_cents: Total for this line (`quantity * unit_amount_cents`)
        tenant_id: The paying tenant (mirrors the parent invoice)
    """

    __tablename__ = "invoice_line_items"  # pyright: ignore[reportAssignmentType]

    invoice_id: UUID = Field(nullable=False, foreign_key="invoices.id", index=True)

    description: str = Field(nullable=False, max_length=500)

    quantity: int = Field(default=1, nullable=False)

    unit_amount_cents: int = Field(nullable=False)

    amount_cents: int = Field(nullable=False)


class InvoiceSequence(BaseModel, table=True):
    """A global, gapless invoice-number counter for one series.

    One row per series (e.g. `"2026"`). `next_number` is incremented via
    `SELECT ... FOR UPDATE` inside the same transaction that creates an
    `Invoice`, never derived from `COUNT(*)` (which races and can gap on
    rollback).

    Attributes:
        id: UUID primary key
        series: The numbering series, e.g. the calendar year `"2026"`
        next_number: The next invoice number to issue in this series
        tenant_id: Always `NULL` — gapless numbering is a per-issuer
            (not per-tenant) compliance requirement
    """

    __tablename__ = "invoice_sequences"  # pyright: ignore[reportAssignmentType]

    series: str = Field(unique=True, index=True, nullable=False, max_length=16)

    next_number: int = Field(default=1, nullable=False)
