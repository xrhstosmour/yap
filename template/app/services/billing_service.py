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
from app.core.payment_provider import PaymentProvider
from app.core.settings import settings
from app.models.audit_log import AuditAction
from app.models.invoice import InvoiceStatus
from app.models.payment import PaymentMethodType
from app.models.payment import PaymentStatus
from app.models.subscription import Subscription
from app.models.subscription import SubscriptionStatus
from app.repositories.audit_repository import AuditLogRepository
from app.repositories.coupon_repository import CouponRedemptionRepository
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.payment_repository import PaymentMethodRepository
from app.repositories.payment_repository import PaymentRepository
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
        self.coupon_redemption_repository = CouponRedemptionRepository(session)

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
        if self.audit_repository is None:
            return
        try:
            async with self.session.begin_nested():
                await self.audit_repository.log(
                    action=AuditAction.INVOICE_ISSUED,
                    actor_id=str(SYSTEM_TENANT_ID),
                    actor_type="system",
                    tenant_id=tenant_id,
                    resource_type="invoice",
                    resource_id=str(invoice_id),
                    metadata={"invoice_number": invoice_number},
                )
        except Exception:
            logger.warning(
                "audit_log_write_failed",
                action=AuditAction.INVOICE_ISSUED.value,
                resource_id=str(invoice_id),
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
    vat_rate = Decimal("0")
    if tax_amounts:
        percentage = (
            (tax_amounts[0].get("tax_rate_details") or {}).get("percentage_decimal")
        )
        if percentage is not None:
            vat_rate = Decimal(str(percentage)) / Decimal("100")

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
