"""Subscription model and lifecycle state machine.

A `Subscription` tracks one tenant's billing lifecycle: trial, active
paid subscription, payment lapse, grace period, and expiry/cancellation.
Unlike most billing models, `tenant_id` is overridden to `NOT NULL`
here (same pattern as `AuditLog.tenant_id`) — a subscription only ever
makes sense in a tenant's context.
"""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Index
from sqlmodel import Field

from app.models.base import BaseModel


class SubscriptionStatus(enum.StrEnum):
    """Subscription lifecycle states.

    `trialing -> active -> past_due -> grace_period -> expired`, with
    `canceled` reachable from `active`/`past_due`/`grace_period`.

    - `trialing`: no Stripe subscription exists yet.
    - `active`: `checkout.session.completed` webhook received.
    - `past_due`: Stripe is retrying a failed payment.
    - `grace_period`: internal-only, assigned by the sweep task once
      `trialing`/`past_due` has passed its natural end but is still
      before `grace_period_ends_at`. Never a Stripe-driven status.
    - `expired`: hard access cutoff, sweep-driven.
    - `canceled`: `customer.subscription.deleted` webhook, or
      portal-initiated cancel.
    """

    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    GRACE_PERIOD = "grace_period"
    EXPIRED = "expired"
    CANCELED = "canceled"


class Subscription(BaseModel, table=True):
    """A tenant's subscription to a `Plan`.

    Attributes:
        id: UUID primary key
        tenant_id: The subscribing tenant. Overridden to `NOT NULL` —
            a subscription only ever exists within a tenant's context.
        plan_id: FK to `Plan`, nullable during pre-checkout trial
        status: Current lifecycle state, see `SubscriptionStatus`
        stripe_customer_id: Stripe `Customer` object ID, nullable
        stripe_subscription_id: Stripe `Subscription` object ID,
            nullable — both nullable so a pure trial (no Stripe
            subscription yet) is representable
        trial_started_at: When the trial began
        trial_ends_at: When the trial naturally ends
        current_period_start: Mirrored from Stripe once a real
            subscription exists
        current_period_end: Mirrored from Stripe once a real
            subscription exists
        grace_period_ends_at: Stored explicitly (not recomputed at
            read time) once a grace period begins
        cancel_at_period_end: Whether the subscription is set to
            cancel at the end of the current billing period
        canceled_at: When cancellation was requested/took effect
        coupon_redemption_id: FK to the `CouponRedemption` applied at
            checkout, nullable
    """

    __tablename__ = "subscriptions"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        # Covers the sweep task's WHERE tenant_id = ... AND status IN (...)
        # query pattern, mirroring OutboxEvent's (status, created_at) index.
        Index("ix_subscriptions_tenant_id_status", "tenant_id", "status"),
    )

    tenant_id: UUID = Field(nullable=False, foreign_key="tenants.id", index=True)

    plan_id: UUID | None = Field(default=None, foreign_key="plans.id", index=True)

    status: SubscriptionStatus = Field(
        nullable=False,
        index=False,
        sa_type=SAEnum(
            SubscriptionStatus,
            values_callable=lambda e: [m.value for m in e],
        ),  # type: ignore[call-overload]
    )

    stripe_customer_id: str | None = Field(default=None, max_length=255, index=True)

    stripe_subscription_id: str | None = Field(default=None, max_length=255, index=True)

    trial_started_at: datetime | None = Field(default=None)

    trial_ends_at: datetime | None = Field(default=None)

    current_period_start: datetime | None = Field(default=None)

    current_period_end: datetime | None = Field(default=None)

    grace_period_ends_at: datetime | None = Field(default=None)

    cancel_at_period_end: bool = Field(default=False, nullable=False)

    canceled_at: datetime | None = Field(default=None)

    coupon_redemption_id: UUID | None = Field(
        default=None, foreign_key="coupon_redemptions.id"
    )


# Non-terminal statuses: a tenant may have at most one subscription in
# one of these states at a time, enforced at the service layer (not a
# DB constraint, since terminal rows — canceled/expired — are kept as
# history and would otherwise conflict with a unique index).
NON_TERMINAL_STATUSES = frozenset(
    {
        SubscriptionStatus.TRIALING,
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.PAST_DUE,
        SubscriptionStatus.GRACE_PERIOD,
    }
)

# Terminal statuses: no further transitions are legal.
TERMINAL_STATUSES = frozenset({SubscriptionStatus.EXPIRED, SubscriptionStatus.CANCELED})
