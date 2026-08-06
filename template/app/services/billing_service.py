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
from app.core.payment_provider import CheckoutSession
from app.core.payment_provider import CouponApplication
from app.core.payment_provider import PaymentProvider
from app.core.settings import settings
from app.models.audit_log import AuditAction
from app.models.coupon import Coupon
from app.models.coupon import CouponDiscountType
from app.models.coupon import CouponRedemption
from app.models.invoice import Invoice
from app.models.invoice import InvoiceStatus
from app.models.payment import Payment
from app.models.payment import PaymentMethod
from app.models.payment import PaymentMethodType
from app.models.payment import PaymentStatus
from app.models.subscription import Subscription
from app.models.subscription import SubscriptionStatus
from app.repositories.audit_repository import AuditLogRepository
from app.repositories.coupon_repository import CouponRedemptionRepository
from app.repositories.coupon_repository import CouponRepository
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.payment_repository import PaymentMethodRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.plan_repository import PlanRepository
from app.repositories.subscription_repository import SubscriptionRepository

logger = get_logger("services.billing")


class BillingServiceError(Exception):
    """Base exception for billing service operations."""


class IllegalSubscriptionTransitionError(BillingServiceError):
    """Raised when a requested status transition is not in `ALLOWED_TRANSITIONS`."""


class PlanNotFoundError(BillingServiceError):
    """Raised when a `Plan` cannot be found or is not active."""


class NoActiveSubscriptionError(BillingServiceError):
    """Raised when an operation requires a subscription that doesn't exist."""


class CouponNotFoundError(BillingServiceError):
    """Raised when a `Coupon` code doesn't exist or isn't active."""


class CouponExpiredError(BillingServiceError):
    """Raised when a `Coupon` is outside its `valid_from`/`valid_until` window."""


class CouponExhaustedError(BillingServiceError):
    """Raised when a `Coupon` has reached `max_redemptions`."""


class CouponNotApplicableError(BillingServiceError):
    """Raised when a `Coupon`'s plan/tenant allow-list excludes this redemption."""


class CouponAlreadyRedeemedError(BillingServiceError):
    """Raised when the tenant already has an active redemption of this `Coupon`."""


class SubscriptionNotFoundError(BillingServiceError):
    """Raised when a subscription cannot be found."""


# Bounded window for the placeholder `Subscription` row `start_checkout`
# creates when a tenant has no non-terminal subscription (first-ever
# checkout racing ahead of provisioning, or resubscribing after
# `expired`/`canceled`). Matches Stripe Checkout Session's own default
# expiry — long enough to complete checkout, short enough that an
# abandoned attempt doesn't grant indefinite free access: the sweep
# picks it up via `trial_ends_at` like any other trial, so it still
# passes through `grace_period` before `expired`, never stalling open.
_CHECKOUT_PENDING_HOURS = 24


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

        # Captured before the write: `BaseRepository.update()` mutates
        # `subscription` in place (same identity-mapped ORM object, same
        # session) rather than returning a distinct instance, so reading
        # `subscription.status` *after* the update would already reflect
        # `new_status`.
        from_status = subscription.status

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
                "from": from_status.value,
                "to": new_status.value,
                "source": source,
            },
            actor_id=actor_id,
        )

        logger.info(
            "subscription_transitioned",
            subscription_id=str(subscription_id),
            from_status=from_status.value,
            to_status=new_status.value,
            source=source,
        )
        return updated


def _unix_to_datetime(value: Any) -> datetime | None:
    """Convert a Stripe unix timestamp (int, possibly `None`) to `datetime`."""
    if value is None:
        return None
    return datetime.fromtimestamp(int(value), tz=UTC)


class BillingService:
    """Orchestrates the billing operations exposed via the API/webhook layer.

    Composes `SubscriptionService` with the plan/coupon/invoice/payment
    repositories and a `PaymentProvider`, following the
    `TenantService`/`FeatureFlagService` pattern: session-only
    constructor, typed exceptions, no FastAPI awareness — HTTP mapping
    happens in the router.
    """

    def __init__(
        self,
        session: AsyncSession,
        payment_provider: PaymentProvider,
        audit_repository: AuditLogRepository | None = None,
    ) -> None:
        self.session = session
        self.payment_provider = payment_provider
        self.audit_repository = audit_repository
        self.subscription_service = SubscriptionService(session, audit_repository)
        self.subscription_repository = SubscriptionRepository(session)
        self.invoice_repository = InvoiceRepository(session)
        self.payment_repository = PaymentRepository(session)
        self.payment_method_repository = PaymentMethodRepository(session)
        self.plan_repository = PlanRepository(session)
        self.coupon_repository = CouponRepository(session)
        self.coupon_redemption_repository = CouponRedemptionRepository(session)

    # -- Coupon validation / redemption ------------------------------------

    async def validate_coupon(
        self, code: str, tenant_id: UUID, plan_id: UUID
    ) -> Coupon:
        """Validate a coupon code without redeeming it.

        Used by `POST /billing/coupons/validate` so the frontend can
        show "code applied" before committing to checkout. Validates in
        order, failing fast with typed errors: active + within
        `valid_from`/`valid_until`, under `max_redemptions`,
        plan/tenant allow-lists satisfied, no existing redemption for
        this tenant.
        """
        coupon = await self.coupon_repository.get_by_code(code)
        if coupon is None or not coupon.is_active:
            raise CouponNotFoundError(f"Coupon '{code}' not found")

        now = datetime.now(UTC)
        if coupon.valid_from is not None and now < coupon.valid_from:
            raise CouponExpiredError(f"Coupon '{code}' is not yet valid")
        if coupon.valid_until is not None and now > coupon.valid_until:
            raise CouponExpiredError(f"Coupon '{code}' has expired")

        if (
            coupon.max_redemptions is not None
            and coupon.redemption_count >= coupon.max_redemptions
        ):
            raise CouponExhaustedError(f"Coupon '{code}' has been fully redeemed")

        # Compared as `str` on both sides: `allowed_plan_ids`/
        # `allowed_tenant_ids` are stored as string UUIDs (plain `JSON`
        # columns can't serialize `UUID` directly), so comparing a
        # `UUID` instance against the list directly always fails.
        if coupon.allowed_plan_ids and str(plan_id) not in {
            str(allowed_plan_id) for allowed_plan_id in coupon.allowed_plan_ids
        }:
            raise CouponNotApplicableError(
                f"Coupon '{code}' is not applicable to this plan"
            )
        if coupon.allowed_tenant_ids and str(tenant_id) not in {
            str(allowed_tenant_id) for allowed_tenant_id in coupon.allowed_tenant_ids
        }:
            raise CouponNotApplicableError(
                f"Coupon '{code}' is not applicable to this tenant"
            )

        existing = await self.coupon_redemption_repository.get_active_for_tenant(
            coupon.id, tenant_id
        )
        if existing is not None:
            raise CouponAlreadyRedeemedError(
                f"Coupon '{code}' has already been redeemed by this tenant"
            )

        return coupon

    async def redeem_coupon(
        self, code: str, tenant_id: UUID, plan_id: UUID, user_id: UUID
    ) -> tuple[Coupon, CouponRedemption]:
        """Validate and redeem a coupon, row-locking the redemption count.

        `CouponRedemption.subscription_id` stays `NULL` until checkout
        completes (`BillingService.handle_checkout_completed` finalizes
        it via the `checkout.session.completed` webhook).
        """
        coupon = await self.validate_coupon(code, tenant_id, plan_id)

        # Row-lock, then re-validate `max_redemptions` under the lock —
        # `validate_coupon` above is a fast pre-check, not itself
        # race-safe against concurrent redemptions of the last slot.
        locked_coupon = await self.coupon_repository.get_for_update(coupon.id)
        if locked_coupon is None:
            raise CouponNotFoundError(f"Coupon '{code}' not found")
        if (
            locked_coupon.max_redemptions is not None
            and locked_coupon.redemption_count >= locked_coupon.max_redemptions
        ):
            raise CouponExhaustedError(f"Coupon '{code}' has been fully redeemed")

        await self.coupon_repository.increment_redemption_count(locked_coupon.id)
        redemption = await self.coupon_redemption_repository.create(
            {
                "tenant_id": tenant_id,
                "coupon_id": locked_coupon.id,
                "redeemed_by_user_id": user_id,
            }
        )

        await self._log_audit(
            action=AuditAction.COUPON_REDEEMED,
            tenant_id=tenant_id,
            resource_type="coupon",
            resource_id=str(locked_coupon.id),
            metadata={"code": locked_coupon.code, "redemption_id": str(redemption.id)},
            actor_id=user_id,
        )

        return locked_coupon, redemption

    # -- Checkout / portal / cancel -----------------------------------------

    async def start_checkout(
        self,
        tenant_id: UUID,
        plan_id: UUID,
        user_id: UUID,
        user_email: str,
        success_url: str,
        cancel_url: str,
        coupon_code: str | None = None,
    ) -> CheckoutSession:
        """Start a Stripe Checkout session for a tenant's plan subscription.

        Normally attaches to the existing `Subscription` row created by
        `SubscriptionService.start_trial` when the tenant was
        provisioned — trial starts immediately with no Stripe object;
        Checkout only happens when the tenant chooses to subscribe or
        the trial is ending. If the tenant has no non-terminal
        subscription (first-ever checkout raced ahead of provisioning,
        or the tenant is resubscribing after `expired`/`canceled`), a
        fresh row is created here instead of raising — per the
        `ALLOWED_TRANSITIONS` module note, a resubscribing tenant always
        gets a brand-new row, never a resurrected terminal one. No new
        trial is granted: `trial_ends_at` is bounded to
        `_CHECKOUT_PENDING_HOURS`, not a real trial length, so an
        abandoned checkout still gets swept to `grace_period`/`expired`
        instead of granting indefinite free access.
        """
        plan = await self.plan_repository.get(plan_id)
        if plan is None or not plan.is_active:
            raise PlanNotFoundError(f"Plan {plan_id} not found")

        subscription = await self.subscription_repository.get_active_for_tenant(
            tenant_id
        )
        if subscription is None:
            checkout_deadline = datetime.now(UTC) + timedelta(
                hours=_CHECKOUT_PENDING_HOURS
            )
            subscription = await self.subscription_repository.create(
                {
                    "tenant_id": tenant_id,
                    "plan_id": plan_id,
                    "status": SubscriptionStatus.TRIALING,
                    "trial_ends_at": checkout_deadline,
                    "grace_period_ends_at": checkout_deadline
                    + timedelta(days=settings.BILLING_GRACE_PERIOD_DAYS),
                }
            )
            await self._log_audit(
                action=AuditAction.SUBSCRIPTION_CREATED,
                tenant_id=tenant_id,
                resource_type="subscription",
                resource_id=str(subscription.id),
                metadata={"status": subscription.status.value, "source": "checkout"},
                actor_id=user_id,
            )

        metadata = {
            "tenant_id": str(tenant_id),
            "subscription_id": str(subscription.id),
        }

        coupon_application: CouponApplication | None = None
        trial_period_days: int | None = None
        redemption: CouponRedemption | None = None
        coupon: Coupon | None = None
        if coupon_code:
            coupon, redemption = await self.redeem_coupon(
                coupon_code, tenant_id, plan_id, user_id
            )
            metadata["coupon_redemption_id"] = str(redemption.id)
            if coupon.discount_type == CouponDiscountType.FREE:
                # A free-period coupon ("free for 14 days") is a trial
                # extension, not a Stripe discount — Stripe coupons have
                # no concept of "waive N days", so this maps directly
                # onto the checkout session's own `trial_period_days`
                # instead of `discounts`. Routing this through
                # `_ensure_stripe_coupon` as an `amount_off=0` discount
                # (the previous behavior) created a real but worthless
                # Stripe coupon and charged the customer immediately —
                # the free days were never actually granted.
                trial_period_days = coupon.free_days or None
            else:
                coupon_application = CouponApplication(
                    code=coupon.code,
                    discount_type=coupon.discount_type.value,
                    percent_off=coupon.percent_off,
                    amount_off_cents=coupon.amount_off_cents,
                    currency=plan.currency,
                    duration=coupon.duration.value,
                    duration_in_months=coupon.duration_in_months,
                    stripe_coupon_id=coupon.stripe_coupon_id,
                )

        checkout_session = await self.payment_provider.create_checkout_session(
            stripe_customer_id=subscription.stripe_customer_id,
            customer_email=user_email,
            price_id=plan.stripe_price_id or "",
            success_url=success_url,
            cancel_url=cancel_url,
            trial_period_days=trial_period_days,
            coupon=coupon_application,
            metadata=metadata,
            # Stable per subscription attempt: a network retry or a
            # double-submitted checkout click for the same subscription
            # returns the already-created session instead of a second
            # one. A genuinely new attempt (after cancel/expiry) always
            # gets a fresh `subscription.id`, so this never goes stale.
            idempotency_key=f"checkout-session-{subscription.id}",
        )

        if coupon is not None and checkout_session.created_stripe_coupon_id:
            await self.coupon_repository.update(
                coupon.id,
                {"stripe_coupon_id": checkout_session.created_stripe_coupon_id},
            )

        return checkout_session

    async def create_portal_session(self, tenant_id: UUID, return_url: str) -> str:
        """Create a Stripe Billing Portal session, returning its URL."""
        subscription = await self.subscription_repository.get_active_for_tenant(
            tenant_id
        )
        if subscription is None or subscription.stripe_customer_id is None:
            raise NoActiveSubscriptionError(
                f"Tenant {tenant_id} has no billable Stripe customer yet"
            )

        # Bucketed by hour rather than keyed purely on `subscription.id`:
        # dedupes a network retry or double-click within the same
        # request, without pinning the tenant to a single portal session
        # (which Stripe expires relatively quickly) for longer than that.
        hour_bucket = datetime.now(UTC).strftime("%Y%m%d%H")
        return await self.payment_provider.create_billing_portal_session(
            stripe_customer_id=subscription.stripe_customer_id,
            return_url=return_url,
            idempotency_key=f"portal-session-{subscription.id}-{hour_bucket}",
        )

    async def cancel_tenant_subscription(
        self,
        tenant_id: UUID,
        actor_id: UUID,
        at_period_end: bool = True,
    ) -> Subscription:
        """Cancel a tenant's subscription, immediately or at period end."""
        subscription = await self.subscription_repository.get_active_for_tenant(
            tenant_id
        )
        if subscription is None or subscription.stripe_subscription_id is None:
            raise NoActiveSubscriptionError(
                f"Tenant {tenant_id} has no active Stripe subscription to cancel"
            )

        await self.payment_provider.cancel_subscription(
            stripe_subscription_id=subscription.stripe_subscription_id,
            at_period_end=at_period_end,
            idempotency_key=f"cancel-{subscription.stripe_subscription_id}-{at_period_end}",
        )

        if at_period_end:
            # The subscription stays `active` until Stripe actually ends
            # it (`customer.subscription.deleted` webhook finalizes the
            # `canceled` transition) — only the intent is recorded now.
            updated = await self.subscription_repository.update(
                subscription.id, {"cancel_at_period_end": True}
            )
            assert updated is not None
            await self._log_audit(
                action=AuditAction.SUBSCRIPTION_STATUS_CHANGED,
                tenant_id=tenant_id,
                resource_type="subscription",
                resource_id=str(subscription.id),
                metadata={"cancel_at_period_end": True, "source": "portal"},
                actor_id=actor_id,
            )
            return updated

        return await self.subscription_service.transition(
            subscription.id,
            SubscriptionStatus.CANCELED,
            source="portal",
            actor_id=actor_id,
        )

    # -- Read-only listings ---------------------------------------------

    async def get_subscription_for_tenant(
        self, tenant_id: UUID
    ) -> Subscription | None:
        return await self.subscription_repository.get_active_for_tenant(tenant_id)

    async def list_invoices_for_tenant(
        self, tenant_id: UUID, skip: int = 0, limit: int = 20
    ) -> tuple[list[Invoice], int]:
        return await self.invoice_repository.list_for_tenant(
            tenant_id, skip=skip, limit=limit
        )

    async def list_payments_for_tenant(
        self, tenant_id: UUID, skip: int = 0, limit: int = 20
    ) -> tuple[list[Payment], int]:
        return await self.payment_repository.list_for_tenant(
            tenant_id, skip=skip, limit=limit
        )

    async def list_payment_methods_for_tenant(
        self, tenant_id: UUID
    ) -> list[PaymentMethod]:
        return await self.payment_method_repository.list_for_tenant(tenant_id)

    # -- Webhook event handlers -------------------------------------------
    #
    # Each handler receives the raw `data.object` dict from a verified
    # Stripe event (see `ProviderEvent.data`). None of these are ever
    # reachable from a client-facing endpoint — the webhook router is
    # their only caller, after signature verification and dedup-insert.

    async def handle_checkout_completed(self, data: dict[str, Any]) -> None:
        """`checkout.session.completed`: attach IDs, `trialing -> active`."""
        metadata = data.get("metadata") or {}
        subscription_id_raw = metadata.get("subscription_id")
        if not subscription_id_raw:
            logger.warning("checkout_completed_missing_subscription_metadata")
            return

        subscription_id = UUID(subscription_id_raw)
        stripe_customer_id = data.get("customer")
        stripe_subscription_id = data.get("subscription")

        await self.subscription_service.transition(
            subscription_id,
            SubscriptionStatus.ACTIVE,
            source="checkout.session.completed",
            extra_fields={
                "stripe_customer_id": stripe_customer_id,
                "stripe_subscription_id": stripe_subscription_id,
            },
        )

        coupon_redemption_id = metadata.get("coupon_redemption_id")
        if coupon_redemption_id:
            await self.coupon_redemption_repository.finalize(
                UUID(coupon_redemption_id), subscription_id
            )

    async def handle_subscription_updated(self, data: dict[str, Any]) -> None:
        """`customer.subscription.updated`: sync status/period, drive active <-> past_due."""
        stripe_subscription_id = data.get("id")
        if not stripe_subscription_id:
            return

        subscription = await self.subscription_repository.get_by_stripe_subscription_id(
            stripe_subscription_id
        )
        if subscription is None:
            logger.warning(
                "subscription_updated_unknown_stripe_subscription",
                stripe_subscription_id=stripe_subscription_id,
            )
            return

        stripe_status = data.get("status")
        extra_fields: dict[str, Any] = {
            "current_period_start": _unix_to_datetime(data.get("current_period_start")),
            "current_period_end": _unix_to_datetime(data.get("current_period_end")),
            "cancel_at_period_end": bool(data.get("cancel_at_period_end", False)),
        }

        target_status: SubscriptionStatus | None = None
        if stripe_status == "active":
            target_status = SubscriptionStatus.ACTIVE
        elif stripe_status == "past_due":
            target_status = SubscriptionStatus.PAST_DUE
            extra_fields["grace_period_ends_at"] = datetime.now(UTC) + timedelta(
                days=settings.BILLING_GRACE_PERIOD_DAYS
            )
        elif stripe_status == "canceled":
            target_status = SubscriptionStatus.CANCELED

        if target_status is None:
            # Unmapped Stripe status (e.g. "incomplete", "unpaid"): still
            # record the refreshed period fields, but leave our status
            # untouched rather than guessing a transition.
            await self.subscription_repository.update(subscription.id, extra_fields)
            return

        await self.subscription_service.transition(
            subscription.id,
            target_status,
            source="customer.subscription.updated",
            extra_fields=extra_fields,
        )

    async def handle_subscription_deleted(self, data: dict[str, Any]) -> None:
        """`customer.subscription.deleted`: `-> canceled`."""
        stripe_subscription_id = data.get("id")
        if not stripe_subscription_id:
            return

        subscription = await self.subscription_repository.get_by_stripe_subscription_id(
            stripe_subscription_id
        )
        if subscription is None:
            return

        await self.subscription_service.transition(
            subscription.id,
            SubscriptionStatus.CANCELED,
            source="customer.subscription.deleted",
        )

    async def handle_invoice_paid(self, data: dict[str, Any]) -> None:
        """`invoice.paid`: mint our sequential invoice, record the `Payment`.

        Deduplicated by `stripe_invoice_id`, on top of (not instead of)
        the webhook-level dedup keyed on the Stripe event ID — a second
        event that maps to the same Stripe invoice (a manually replayed
        event, or a future event type covering the same invoice) must
        not mint a second sequential number or duplicate the `Payment`
        ledger row.
        """
        stripe_invoice_id = data.get("id")
        if stripe_invoice_id:
            existing_invoice = await self.invoice_repository.get_by_stripe_invoice_id(
                stripe_invoice_id
            )
            if existing_invoice is not None:
                logger.info(
                    "invoice_paid_duplicate_ignored",
                    stripe_invoice_id=stripe_invoice_id,
                )
                return

        stripe_subscription_id = data.get("subscription")
        subscription = None
        if stripe_subscription_id:
            subscription = await self.subscription_repository.get_by_stripe_subscription_id(
                stripe_subscription_id
            )

        tenant_id = subscription.tenant_id if subscription else None
        if tenant_id is None:
            logger.warning(
                "invoice_paid_unknown_subscription",
                stripe_subscription_id=stripe_subscription_id,
            )
            return

        vat_fields = _extract_vat_fields(data)
        issue_date = (_unix_to_datetime(data.get("created")) or datetime.now(UTC)).date()
        series = str(issue_date.year)

        invoice = await self.invoice_repository.issue_invoice(
            series,
            {
                "tenant_id": tenant_id,
                "subscription_id": subscription.id if subscription else None,
                "stripe_invoice_id": data.get("id"),
                "status": InvoiceStatus.PAID,
                "issue_date": issue_date,
                "amount_due_cents": data.get("amount_due") or 0,
                "amount_paid_cents": data.get("amount_paid") or 0,
                "currency": data.get("currency") or "eur",
                "paid_at": datetime.now(UTC),
                "hosted_invoice_url": data.get("hosted_invoice_url"),
                **vat_fields,
            },
        )

        await self.payment_repository.create(
            {
                "tenant_id": tenant_id,
                "invoice_id": invoice.id,
                "subscription_id": subscription.id if subscription else None,
                "provider": "stripe",
                "stripe_payment_intent_id": data.get("payment_intent"),
                "stripe_charge_id": data.get("charge"),
                "amount_cents": data.get("amount_paid") or 0,
                "currency": data.get("currency") or "eur",
                "status": PaymentStatus.SUCCEEDED,
                "paid_at": datetime.now(UTC),
            }
        )

        await self._log_invoice_issued_audit(tenant_id, invoice.id, invoice.invoice_number)

        if subscription is not None:
            await self.subscription_service.transition(
                subscription.id,
                SubscriptionStatus.ACTIVE,
                source="invoice.paid",
            )

    async def handle_invoice_payment_failed(self, data: dict[str, Any]) -> None:
        """`invoice.payment_failed`: record the failed `Payment`, start the grace clock."""
        stripe_subscription_id = data.get("subscription")
        subscription = None
        if stripe_subscription_id:
            subscription = await self.subscription_repository.get_by_stripe_subscription_id(
                stripe_subscription_id
            )

        tenant_id = subscription.tenant_id if subscription else None
        if tenant_id is None:
            logger.warning(
                "invoice_payment_failed_unknown_subscription",
                stripe_subscription_id=stripe_subscription_id,
            )
            return

        last_error = data.get("last_finalization_error") or {}

        await self.payment_repository.create(
            {
                "tenant_id": tenant_id,
                "subscription_id": subscription.id if subscription else None,
                "provider": "stripe",
                "stripe_payment_intent_id": data.get("payment_intent"),
                "amount_cents": data.get("amount_due") or 0,
                "currency": data.get("currency") or "eur",
                "status": PaymentStatus.FAILED,
                "failure_code": last_error.get("code"),
                "failure_message": last_error.get("message"),
            }
        )

        if subscription is not None:
            await self.subscription_service.transition(
                subscription.id,
                SubscriptionStatus.PAST_DUE,
                source="invoice.payment_failed",
                extra_fields={
                    "grace_period_ends_at": datetime.now(UTC)
                    + timedelta(days=settings.BILLING_GRACE_PERIOD_DAYS)
                },
            )

    async def handle_charge_refunded(self, data: dict[str, Any]) -> None:
        """`charge.refunded`: update the matching `Payment`'s refund fields."""
        stripe_charge_id = data.get("id")
        if not stripe_charge_id:
            return

        payment = await self.payment_repository.get_by_stripe_charge_id(
            stripe_charge_id
        )
        if payment is None:
            logger.warning(
                "charge_refunded_unknown_payment", stripe_charge_id=stripe_charge_id
            )
            return

        refunded_amount = data.get("amount_refunded") or 0
        amount = data.get("amount") or payment.amount_cents
        status = (
            PaymentStatus.REFUNDED
            if refunded_amount >= amount
            else PaymentStatus.PARTIALLY_REFUNDED
        )

        await self.payment_repository.update(
            payment.id,
            {
                "status": status,
                "refunded_amount_cents": refunded_amount,
                "refunded_at": datetime.now(UTC),
            },
        )

    async def handle_payment_method_attached(self, data: dict[str, Any]) -> None:
        """`payment_method.attached`: upsert the `PaymentMethod` row."""
        stripe_customer_id = data.get("customer")
        stripe_payment_method_id = data.get("id")
        if not stripe_customer_id or not stripe_payment_method_id:
            return

        subscription = await self.subscription_repository.get_by_stripe_customer_id(
            stripe_customer_id
        )
        if subscription is None:
            logger.warning(
                "payment_method_attached_unknown_customer",
                stripe_customer_id=stripe_customer_id,
            )
            return

        card = data.get("card") or {}
        method_type = data.get("type") or "card"

        existing = await self.payment_method_repository.get_by_stripe_payment_method_id(
            stripe_payment_method_id
        )
        fields = {
            "tenant_id": subscription.tenant_id,
            "stripe_payment_method_id": stripe_payment_method_id,
            "type": PaymentMethodType.CARD
            if method_type == "card"
            else PaymentMethodType.SEPA_DEBIT,
            "brand": card.get("brand"),
            "last_four": card.get("last4"),
            "exp_month": card.get("exp_month"),
            "exp_year": card.get("exp_year"),
        }
        if existing:
            await self.payment_method_repository.update(existing.id, fields)
        else:
            await self.payment_method_repository.create(fields)

    async def handle_payment_method_detached(self, data: dict[str, Any]) -> None:
        """`payment_method.detached`: remove the `PaymentMethod` row."""
        stripe_payment_method_id = data.get("id")
        if not stripe_payment_method_id:
            return

        existing = await self.payment_method_repository.get_by_stripe_payment_method_id(
            stripe_payment_method_id
        )
        if existing:
            await self.payment_method_repository.delete(existing.id, hard=True)

    async def handle_customer_updated(self, data: dict[str, Any]) -> None:
        """`customer.updated`: sync which `PaymentMethod` is the default."""
        stripe_customer_id = data.get("id")
        invoice_settings = data.get("invoice_settings") or {}
        default_payment_method = invoice_settings.get("default_payment_method")
        if not stripe_customer_id or not default_payment_method:
            return

        subscription = await self.subscription_repository.get_by_stripe_customer_id(
            stripe_customer_id
        )
        if subscription is None:
            return

        await self.payment_method_repository.clear_default_for_tenant(
            subscription.tenant_id
        )
        default = await self.payment_method_repository.get_by_stripe_payment_method_id(
            default_payment_method
        )
        if default:
            await self.payment_method_repository.update(default.id, {"is_default": True})

    async def _log_invoice_issued_audit(
        self, tenant_id: UUID, invoice_id: UUID, invoice_number: str
    ) -> None:
        await self._log_audit(
            action=AuditAction.INVOICE_ISSUED,
            tenant_id=tenant_id,
            resource_type="invoice",
            resource_id=str(invoice_id),
            metadata={"invoice_number": invoice_number},
            actor_id=None,
        )

    async def _log_audit(
        self,
        action: AuditAction,
        tenant_id: UUID,
        resource_type: str,
        resource_id: str,
        metadata: dict[str, Any],
        actor_id: UUID | None,
    ) -> None:
        """Write an audit entry, swallowing failures (see `AuditLogRepository`).

        `actor_id=None` logs as `actor_type="system"` under the
        system-tenant fallback actor (webhook-driven writes, e.g.
        `INVOICE_ISSUED`); a real `actor_id` logs as `actor_type="user"`
        (user-initiated writes, e.g. `COUPON_REDEEMED`).
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
                    resource_type=resource_type,
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


def _extract_vat_fields(invoice_data: dict[str, Any]) -> dict[str, Any]:
    """Best-effort VAT field extraction from a raw Stripe invoice webhook payload.

    Entirely delegated to Stripe Tax — this only reads back whatever it
    already decided. Defensive against schema variation across Stripe
    API versions/Tax configurations.
    """
    from decimal import Decimal

    tax_amounts = invoice_data.get("total_tax_amounts") or []
    vat_amount_cents = sum(t.get("amount", 0) or 0 for t in tax_amounts)
    vat_rate = Decimal(0)
    if tax_amounts:
        percentage = (
            (tax_amounts[0].get("tax_rate_details") or {}).get("percentage_decimal")
        )
        if percentage is not None:
            vat_rate = Decimal(str(percentage)) / Decimal(100)

    automatic_tax = invoice_data.get("automatic_tax") or {}
    reverse_charge = automatic_tax.get("status") == "reverse_charge"

    customer_address = invoice_data.get("customer_address") or {}
    customer_tax_ids = invoice_data.get("customer_tax_ids") or []
    vat_id = customer_tax_ids[0].get("value") if customer_tax_ids else None

    return {
        "vat_rate": vat_rate,
        "vat_amount_cents": vat_amount_cents,
        "vat_id": vat_id,
        "customer_country": customer_address.get("country"),
        "reverse_charge": reverse_charge,
        "billing_name": invoice_data.get("customer_name"),
        "billing_address": customer_address,
    }
