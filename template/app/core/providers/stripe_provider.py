"""Stripe implementation of the `PaymentProvider` protocol.

Only Stripe Checkout Session + Billing Portal URLs are used here — no
card data ever transits our own API (PCI SAQ-A). This is a hard
invariant: do not add endpoints that accept raw card details.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any
from typing import cast

import stripe

from app.core.logging import get_logger
from app.core.payment_provider import CheckoutSession
from app.core.payment_provider import CouponApplication
from app.core.payment_provider import PaymentProviderError
from app.core.payment_provider import ProviderEvent
from app.core.payment_provider import ProviderInvoice
from app.core.settings import settings

logger = get_logger("core.stripe_provider")

# Re-exported so callers can catch the specific Stripe exception without
# importing the `stripe` SDK directly in the webhook router.
SignatureVerificationError = stripe.SignatureVerificationError

# Retries Stripe's own SDK considers safe to make automatically (network
# failures on otherwise-idempotent requests), on top of the idempotency
# keys this provider passes explicitly for our own retries/double-submits.
_MAX_NETWORK_RETRIES = 2


@asynccontextmanager
async def _translate_stripe_errors(operation: str) -> AsyncIterator[None]:
    """Turn any `stripe.StripeError` into a provider-agnostic `PaymentProviderError`.

    Callers (`BillingService`, API routes) never need to import the
    `stripe` SDK to handle a provider failure. The original exception is
    logged in full server-side (never returned to the client — the
    global exception handler already reduces unhandled exceptions to a
    generic message) via `raise ... from error` for local debugging.
    """
    try:
        yield
    except stripe.StripeError as error:
        logger.error(
            "stripe_api_error",
            operation=operation,
            error_type=type(error).__name__,
            message=str(error),
        )
        raise PaymentProviderError(
            f"Stripe API error during {operation}: {type(error).__name__}"
        ) from error


class StripeProvider:
    """Stripe-backed `PaymentProvider` implementation."""

    def __init__(
        self,
        api_key: str | None = None,
        webhook_secret: str | None = None,
    ) -> None:
        self._api_key = api_key or settings.STRIPE_API_KEY
        self._webhook_secret = webhook_secret or settings.STRIPE_WEBHOOK_SECRET
        self._client = stripe.StripeClient(
            self._api_key,
            # Pinned explicitly rather than trusting the Stripe account's
            # dashboard-configured default, so a future Stripe-side
            # default-version bump can't silently change response shapes
            # under us. `stripe.api_version` is the version this SDK
            # release itself targets, so it tracks the installed
            # `stripe` package rather than drifting from it.
            stripe_version=stripe.api_version,
            max_network_retries=_MAX_NETWORK_RETRIES,
        )

    async def _ensure_stripe_coupon(self, coupon: CouponApplication) -> str:
        """Return `coupon.stripe_coupon_id`, creating it in Stripe on first use."""
        if coupon.stripe_coupon_id:
            return coupon.stripe_coupon_id

        params: dict[str, Any] = {
            "name": coupon.code,
            "duration": coupon.duration,
        }
        if coupon.duration == "repeating" and coupon.duration_in_months:
            params["duration_in_months"] = coupon.duration_in_months
        if coupon.discount_type == "percent" and coupon.percent_off is not None:
            params["percent_off"] = coupon.percent_off
        elif coupon.discount_type == "fixed_amount":
            params["amount_off"] = coupon.amount_off_cents
            params["currency"] = coupon.currency or "eur"

        # Keyed on our own unique `Coupon.code` — retrying this call for
        # the same code can never create a second Stripe `Coupon` object.
        options = {"idempotency_key": f"coupon-create-{coupon.code}"}
        async with _translate_stripe_errors("coupon creation"):
            created = await self._client.v1.coupons.create_async(
                cast(Any, params), options=cast(Any, options)
            )
        logger.info(
            "stripe_coupon_created", code=coupon.code, stripe_coupon_id=created.id
        )
        return created.id

    async def create_checkout_session(
        self,
        *,
        stripe_customer_id: str | None,
        customer_email: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
        trial_period_days: int | None = None,
        coupon: CouponApplication | None = None,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> CheckoutSession:
        params: dict[str, Any] = {
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            # Delegates VAT/OSS rate determination and EU VAT ID (VIES)
            # reverse-charge validation entirely to Stripe Tax — this
            # codebase never calculates a rate or validates a VAT ID
            # itself. `billing_address_collection` is required for
            # `automatic_tax` to have an address to base the OSS rate
            # on. Without these three, Stripe Tax never runs and every
            # VAT field mirrored onto our `Invoice` rows stays empty.
            "automatic_tax": {"enabled": True},
            "tax_id_collection": {"enabled": True},
            "billing_address_collection": "required",
        }
        if stripe_customer_id:
            params["customer"] = stripe_customer_id
            # Only valid alongside an existing `customer` — Checkout
            # can't update a customer that doesn't exist yet when
            # `customer_email` creates one implicitly.
            params["customer_update"] = {"address": "auto", "name": "auto"}
        else:
            params["customer_email"] = customer_email
        if metadata:
            params["metadata"] = metadata

        subscription_data: dict[str, Any] = {}
        if trial_period_days:
            subscription_data["trial_period_days"] = trial_period_days
        if subscription_data:
            params["subscription_data"] = subscription_data

        created_stripe_coupon_id: str | None = None
        if coupon is not None:
            stripe_coupon_id = await self._ensure_stripe_coupon(coupon)
            if not coupon.stripe_coupon_id:
                created_stripe_coupon_id = stripe_coupon_id
            params["discounts"] = [{"coupon": stripe_coupon_id}]

        options = {"idempotency_key": idempotency_key} if idempotency_key else None
        async with _translate_stripe_errors("checkout session creation"):
            session = await self._client.v1.checkout.sessions.create_async(
                cast(Any, params), options=cast(Any, options)
            )

        return CheckoutSession(
            id=session.id,
            url=session.url or "",
            stripe_customer_id=cast(str | None, session.customer),
            created_stripe_coupon_id=created_stripe_coupon_id,
        )

    async def create_billing_portal_session(
        self,
        *,
        stripe_customer_id: str,
        return_url: str,
        idempotency_key: str | None = None,
    ) -> str:
        options = {"idempotency_key": idempotency_key} if idempotency_key else None
        async with _translate_stripe_errors("billing portal session creation"):
            portal_session = await self._client.v1.billing_portal.sessions.create_async(
                cast(Any, {"customer": stripe_customer_id, "return_url": return_url}),
                options=cast(Any, options),
            )
        return portal_session.url

    async def cancel_subscription(
        self,
        *,
        stripe_subscription_id: str,
        at_period_end: bool = True,
        idempotency_key: str | None = None,
    ) -> None:
        options = {"idempotency_key": idempotency_key} if idempotency_key else None
        async with _translate_stripe_errors("subscription cancellation"):
            if at_period_end:
                await self._client.v1.subscriptions.update_async(
                    stripe_subscription_id,
                    cast(Any, {"cancel_at_period_end": True}),
                    options=cast(Any, options),
                )
            else:
                await self._client.v1.subscriptions.cancel_async(
                    stripe_subscription_id, options=cast(Any, options)
                )

    async def retrieve_invoice(self, *, stripe_invoice_id: str) -> ProviderInvoice:
        async with _translate_stripe_errors("invoice retrieval"):
            invoice = await self._client.v1.invoices.retrieve_async(stripe_invoice_id)

        # VAT rate/reverse-charge determination is entirely delegated to
        # Stripe Tax — this only reads back whatever it already decided.
        # The exact shape of `total_tax_amounts` / `automatic_tax` can
        # vary by Stripe API version and Tax configuration, so this is
        # deliberately defensive rather than assuming a fixed schema.
        vat_rate: Decimal | None = None
        vat_amount_cents = 0
        tax_amounts = getattr(invoice, "total_tax_amounts", None) or []
        if tax_amounts:
            first_tax = tax_amounts[0]
            vat_amount_cents = sum(getattr(t, "amount", 0) or 0 for t in tax_amounts)
            tax_rate_details = getattr(first_tax, "tax_rate_details", None)
            percentage = getattr(tax_rate_details, "percentage_decimal", None)
            if percentage is not None:
                vat_rate = Decimal(str(percentage)) / Decimal(100)

        automatic_tax = getattr(invoice, "automatic_tax", None)
        reverse_charge = getattr(automatic_tax, "status", None) == "reverse_charge"

        customer_address = getattr(invoice, "customer_address", None)
        customer_country = getattr(customer_address, "country", None)
        billing_address = (
            customer_address.to_dict()  # type: ignore[union-attr]
            if customer_address
            else {}
        )

        vat_id = None
        customer_tax_ids = getattr(invoice, "customer_tax_ids", None) or []
        if customer_tax_ids:
            vat_id = getattr(customer_tax_ids[0], "value", None)

        invoice_lines = getattr(invoice, "lines", None)
        line_items = [
            {
                "description": line.description or "",
                "quantity": line.quantity or 1,
                "unit_amount_cents": (line.amount or 0) // max(line.quantity or 1, 1),
                "amount_cents": line.amount or 0,
            }
            for line in (invoice_lines.data if invoice_lines is not None else [])
        ]

        return ProviderInvoice(
            id=invoice.id or "",
            status=invoice.status or "draft",
            amount_due_cents=invoice.amount_due or 0,
            amount_paid_cents=invoice.amount_paid or 0,
            currency=invoice.currency or "eur",
            vat_rate=vat_rate,
            vat_amount_cents=vat_amount_cents,
            vat_id=vat_id,
            customer_country=customer_country,
            reverse_charge=reverse_charge,
            billing_name=getattr(invoice, "customer_name", None),
            billing_address=billing_address,
            hosted_invoice_url=invoice.hosted_invoice_url,
            line_items=line_items,
        )

    def verify_webhook_signature(
        self, *, payload: bytes, signature_header: str
    ) -> ProviderEvent:
        event = self._client.construct_event(
            payload=payload,
            sig_header=signature_header,
            secret=self._webhook_secret,
        )
        event_dict = event.to_dict()
        data_object = event_dict.get("data", {}).get("object", {})
        return ProviderEvent(id=event.id, type=event.type, data=data_object)
