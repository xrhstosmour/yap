"""Billing service: subscription lifecycle, coupons, checkout orchestration.

`SubscriptionService` owns the `SubscriptionStatus` state machine —
every status transition, whether webhook-driven or sweep-driven, goes
through `SubscriptionService.transition()`. `BillingService` composes
`SubscriptionService` with the plan/coupon/invoice repositories and the
`PaymentProvider` to orchestrate the higher-level billing operations
(checkout, coupon redemption, cancellation) exposed via the API layer,
following the `TenantService`/`FeatureFlagService` pattern: session-only
constructor, typed exceptions, no FastAPI awareness.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import SYSTEM_TENANT_ID
from app.core.logging import get_logger
from app.core.settings import settings
from app.models.audit_log import AuditAction
from app.models.subscription import Subscription
from app.models.subscription import SubscriptionStatus
from app.repositories.audit_repository import AuditLogRepository
from app.repositories.subscription_repository import SubscriptionRepository

logger = get_logger("services.billing")


class BillingServiceError(Exception):
    """Base exception for billing service operations."""


class IllegalSubscriptionTransitionError(BillingServiceError):
    """Raised when a requested status transition is not in `ALLOWED_TRANSITIONS`."""


class SubscriptionNotFoundError(BillingServiceError):
    """Raised when a subscription cannot be found."""


# Explicit allowed-transitions table for `SubscriptionStatus`. Terminal
# statuses (`expired`, `canceled`) have no outgoing transitions — a
# tenant that wants to resubscribe after cancellation/expiry gets a
# brand-new `Subscription` row (see the "at most one non-terminal
# subscription per tenant" invariant), not a resurrected old one.
#
# `active` is reachable from every non-terminal status: Stripe is the
# source of truth for "payment succeeded", so a `checkout.session.completed`
# or `invoice.paid` webhook must always be able to flip a subscription
# back to `active` regardless of which non-terminal state it was in.
ALLOWED_TRANSITIONS: dict[SubscriptionStatus, frozenset[SubscriptionStatus]] = {
    SubscriptionStatus.TRIALING: frozenset(
        {
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.PAST_DUE,
            SubscriptionStatus.GRACE_PERIOD,
            SubscriptionStatus.EXPIRED,
            SubscriptionStatus.CANCELED,
        }
    ),
    SubscriptionStatus.PAST_DUE: frozenset(
        {
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.GRACE_PERIOD,
            SubscriptionStatus.EXPIRED,
            SubscriptionStatus.CANCELED,
        }
    ),
    SubscriptionStatus.GRACE_PERIOD: frozenset(
        {
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.EXPIRED,
            SubscriptionStatus.CANCELED,
        }
    ),
    SubscriptionStatus.ACTIVE: frozenset(
        {SubscriptionStatus.PAST_DUE, SubscriptionStatus.CANCELED}
    ),
    SubscriptionStatus.EXPIRED: frozenset(),
    SubscriptionStatus.CANCELED: frozenset(),
}


class SubscriptionService:
    """Owns the `Subscription` lifecycle state machine.

    All status transitions — webhook-driven or sweep-driven — go
    through `transition()`, which row-locks the subscription, validates
    against `ALLOWED_TRANSITIONS`, and writes inside the lock.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit_repository: AuditLogRepository | None = None,
    ) -> None:
        self.session = session
        self.subscription_repository = SubscriptionRepository(session)
        self.audit_repository = audit_repository

    async def _log_audit_safe(
        self,
        action: AuditAction,
        tenant_id: UUID,
        resource_id: str,
        metadata: dict[str, Any],
        actor_id: UUID | None,
    ) -> None:
        """Write an audit entry, swallowing failures (see `AuditLogRepository`).

        Webhook- and sweep-driven transitions have no acting `User` —
        they log with `actor_type="system"` under the system-tenant
        fallback actor, matching the pattern established in
        `TenantService`. User-initiated transitions (e.g. a
        portal-initiated cancel resolved to a specific user) log as
        `actor_type="user"`.
        """
        if self.audit_repository is None:
            return
        try:
            async with self.session.begin_nested():
                await self.audit_repository.log(
                    action=action,
                    actor_id=str(actor_id or SYSTEM_TENANT_ID),
                    actor_type="user" if actor_id else "system",
                    tenant_id=tenant_id,
                    resource_type="subscription",
                    resource_id=resource_id,
                    metadata=metadata,
                )
        except Exception:
            logger.warning(
                "audit_log_write_failed",
                action=action.value,
                resource_id=resource_id,
                exc_info=True,
            )

    async def start_trial(
        self,
        tenant_id: UUID,
        plan_id: UUID | None = None,
        trial_days: int | None = None,
        actor_id: UUID | None = None,
    ) -> Subscription:
        """Start a trial subscription for a tenant.

        No Stripe subscription exists yet — Checkout only happens when
        the tenant chooses to subscribe or the trial is ending.
        """
        now = datetime.now(UTC)
        days = trial_days if trial_days is not None else settings.DEFAULT_TRIAL_DAYS
        trial_ends_at = now + timedelta(days=days)
        grace_period_ends_at = trial_ends_at + timedelta(
            days=settings.BILLING_GRACE_PERIOD_DAYS
        )

        subscription = await self.subscription_repository.create(
            {
                "tenant_id": tenant_id,
                "plan_id": plan_id,
                "status": SubscriptionStatus.TRIALING,
                "trial_started_at": now,
                "trial_ends_at": trial_ends_at,
                "grace_period_ends_at": grace_period_ends_at,
            }
        )

        await self._log_audit_safe(
            action=AuditAction.SUBSCRIPTION_CREATED,
            tenant_id=tenant_id,
            resource_id=str(subscription.id),
            metadata={"status": subscription.status.value},
            actor_id=actor_id,
        )

        logger.info(
            "trial_started",
            tenant_id=str(tenant_id),
            subscription_id=str(subscription.id),
        )
        return subscription

    async def transition(
        self,
        subscription_id: UUID,
        new_status: SubscriptionStatus,
        source: str,
        actor_id: UUID | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> Subscription:
        """Transition a subscription to `new_status`.

        Row-locks the subscription (`SELECT ... FOR UPDATE`), validates
        the transition against `ALLOWED_TRANSITIONS`, and writes inside
        the lock. Idempotent: requesting the status the row is already
        in is a no-op success rather than an error, so replayed
        webhooks and repeated sweep passes are harmless.

        Args:
            subscription_id: The subscription to transition
            new_status: The target status
            source: What triggered this transition (e.g. a Stripe event
                type, or `"sweep"`), recorded in the audit log
            actor_id: Acting user, if any. Webhook/sweep-driven
                transitions leave this `None` and log as `system`.
            extra_fields: Additional column values to set alongside the
                status change (e.g. `stripe_subscription_id`,
                `current_period_end`)

        Raises:
            SubscriptionNotFoundError: If no such subscription exists
            IllegalSubscriptionTransitionError: If the transition is
                not in `ALLOWED_TRANSITIONS`
        """
        subscription = await self.subscription_repository.get_for_update(
            subscription_id
        )
        if subscription is None:
            raise SubscriptionNotFoundError(f"Subscription {subscription_id} not found")

        if subscription.status == new_status:
            # Idempotent no-op: same-status transition requested again
            # (replayed webhook, repeated sweep pass). Still apply any
            # accompanying field updates (e.g. a refreshed period end).
            if extra_fields:
                refreshed = await self.subscription_repository.update(
                    subscription_id, dict(extra_fields)
                )
                return refreshed or subscription
            return subscription

        allowed = ALLOWED_TRANSITIONS.get(subscription.status, frozenset())
        if new_status not in allowed:
            raise IllegalSubscriptionTransitionError(
                f"Cannot transition subscription {subscription_id} "
                f"from {subscription.status.value} to {new_status.value}"
            )

        update_data: dict[str, Any] = {"status": new_status}
        if extra_fields:
            update_data.update(extra_fields)
        if new_status == SubscriptionStatus.CANCELED:
            update_data.setdefault("canceled_at", datetime.now(UTC))

        updated = await self.subscription_repository.update(subscription_id, update_data)
        if updated is None:
            raise SubscriptionNotFoundError(f"Subscription {subscription_id} not found")

        action = (
            AuditAction.SUBSCRIPTION_CANCELED
            if new_status == SubscriptionStatus.CANCELED
            else AuditAction.SUBSCRIPTION_STATUS_CHANGED
        )
        await self._log_audit_safe(
            action=action,
            tenant_id=subscription.tenant_id,
            resource_id=str(subscription_id),
            metadata={
                "from": subscription.status.value,
                "to": new_status.value,
                "source": source,
            },
            actor_id=actor_id,
        )

        logger.info(
            "subscription_transitioned",
            subscription_id=str(subscription_id),
            from_status=subscription.status.value,
            to_status=new_status.value,
            source=source,
        )
        return updated
