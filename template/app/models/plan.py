"""Plan model for the global billing catalog.

A `Plan` is a purchasable subscription tier. Plans are a global catalog,
not tenant-scoped — every tenant sees the same set of plans, so
`tenant_id` is always left `NULL` on this model. See
`app.repositories.plan_repository.PlanRepository` for the deliberate
tenant-filter bypass this requires.
"""

from __future__ import annotations

import enum
from typing import Any

from sqlalchemy import JSON
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field

from app.core.settings import settings
from app.models.base import BaseModel


class BillingInterval(enum.StrEnum):
    """Recurring billing cadence for a `Plan`."""

    MONTH = "month"
    YEAR = "year"


class Plan(BaseModel, table=True):
    """A purchasable subscription plan in the global billing catalog.

    Attributes:
        id: UUID primary key
        name: Unique, human-readable plan name (e.g. "Pro")
        stripe_price_id: Stripe `Price` object ID, nullable until synced
        stripe_product_id: Stripe `Product` object ID, nullable until synced
        amount_cents: Price in the smallest currency unit
        currency: ISO 4217 currency code, lowercase (default "eur")
        billing_interval: Recurring cadence (month/year)
        trial_days: Trial length for this plan; falls back to
            `settings.DEFAULT_TRIAL_DAYS` when not overridden
        is_active: Whether the plan can currently be subscribed to
        features: JSON entitlement flags, kept generic since no
            entitlement gates exist yet in the baseline
        tenant_id: Always `NULL` — plans are a global catalog, not
            tenant-scoped. Inherited from `BaseModel` for consistency
            with the rest of the schema (id/timestamps/soft-delete),
            not because plans belong to a tenant.
    """

    __tablename__ = "plans"  # pyright: ignore[reportAssignmentType]

    name: str = Field(
        unique=True,
        index=True,
        nullable=False,
        max_length=255,
    )

    stripe_price_id: str | None = Field(default=None, max_length=255, index=True)

    stripe_product_id: str | None = Field(default=None, max_length=255)

    amount_cents: int = Field(nullable=False)

    currency: str = Field(default="eur", nullable=False, max_length=3)

    billing_interval: BillingInterval = Field(
        nullable=False,
        sa_type=SAEnum(
            BillingInterval,
            values_callable=lambda e: [m.value for m in e],
        ),  # type: ignore[call-overload]
    )

    trial_days: int = Field(default_factory=lambda: settings.DEFAULT_TRIAL_DAYS)

    is_active: bool = Field(default=True, nullable=False)

    features: dict[str, Any] = Field(
        default_factory=dict,
        sa_type=JSON,
        nullable=False,
    )
