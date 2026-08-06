"""Subscription repository for database operations."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import and_
from sqlmodel import or_
from sqlmodel import select

from app.core.logging import get_logger
from app.models.subscription import NON_TERMINAL_STATUSES
from app.models.subscription import Subscription
from app.models.subscription import SubscriptionStatus
from app.repositories.base import BaseRepository

logger = get_logger("repository.subscription")


class SubscriptionRepository(BaseRepository[Subscription]):
    """Repository for `Subscription` model operations."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Subscription)

    async def get_for_update(self, subscription_id: UUID) -> Subscription | None:
        """Row-lock a subscription for a state transition.

        Bypasses tenant filtering deliberately — callers (webhook
        handlers, the sweep task) resolve `subscription_id` from a
        trusted source (Stripe object ID lookup, or the sweep query
        itself), not from client-supplied tenant context.
        """
        query = (
            select(Subscription)
            .where(Subscription.id == subscription_id)  # type: ignore[arg-type]
            .with_for_update()
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_active_for_tenant(self, tenant_id: UUID) -> Subscription | None:
        """The tenant's current non-terminal subscription, if any."""
        query = select(Subscription).where(
            and_(
                Subscription.tenant_id == tenant_id,  # type: ignore[arg-type]
                Subscription.status.in_(NON_TERMINAL_STATUSES),  # type: ignore[attr-defined]
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_most_recent_for_tenant(
        self, tenant_id: UUID
    ) -> Subscription | None:
        """The tenant's most recently created subscription, terminal or not.

        Used by the access-gating dependency, which must be able to see
        an `expired` row (excluded from `get_active_for_tenant`, which
        only ever returns non-terminal rows) in order to enforce the
        hard cutoff.
        """
        query = (
            select(Subscription)
            .where(Subscription.tenant_id == tenant_id)  # type: ignore[arg-type]
            .order_by(Subscription.created_at.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_stripe_customer_id(
        self, stripe_customer_id: str
    ) -> Subscription | None:
        """Look up by Stripe customer ID, bypassing tenant filtering.

        Used by payment-method sync webhooks, which identify the
        tenant via the Stripe customer, not the subscription.
        """
        query = select(Subscription).where(
            Subscription.stripe_customer_id == stripe_customer_id  # type: ignore[arg-type]
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_stripe_subscription_id(
        self, stripe_subscription_id: str
    ) -> Subscription | None:
        """Look up by Stripe subscription ID, bypassing tenant filtering.

        Webhook handlers resolve the subscription before any tenant
        context is established.
        """
        query = select(Subscription).where(
            Subscription.stripe_subscription_id == stripe_subscription_id  # type: ignore[arg-type]
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def list_due_for_sweep(self, now: datetime, limit: int = 500) -> list[
        Subscription
    ]:
        """Rows the lifecycle sweep should act on, locked and skip-locked.

        Deliberately bypasses `_apply_tenant_filter`'s no-tenant-context
        behavior — the sweep task must see all tenants, on purpose, not
        by accident. `FOR UPDATE SKIP LOCKED` lets concurrent sweep runs
        (redbeat's at-least-once scheduling) divide the work instead of
        blocking on each other.

        Matches: `trialing`/`past_due` subscriptions whose
        `trial_ends_at`/`grace_period_ends_at` has passed, or
        `grace_period` subscriptions whose `grace_period_ends_at` has
        passed.
        """
        query = (
            select(Subscription)
            .where(
                or_(
                    and_(
                        Subscription.status == SubscriptionStatus.TRIALING,  # type: ignore[arg-type]
                        Subscription.trial_ends_at.is_not(None),  # type: ignore[union-attr]
                        Subscription.trial_ends_at <= now,  # type: ignore[operator]
                    ),
                    and_(
                        Subscription.status == SubscriptionStatus.PAST_DUE,  # type: ignore[arg-type]
                        Subscription.grace_period_ends_at.is_not(None),  # type: ignore[union-attr]
                        Subscription.grace_period_ends_at <= now,  # type: ignore[operator]
                    ),
                    and_(
                        Subscription.status == SubscriptionStatus.GRACE_PERIOD,  # type: ignore[arg-type]
                        Subscription.grace_period_ends_at.is_not(None),  # type: ignore[union-attr]
                        Subscription.grace_period_ends_at <= now,  # type: ignore[operator]
                    ),
                )
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())
