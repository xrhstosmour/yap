"""Payment provider abstraction.

`PaymentProvider` is a small `Protocol` (matches the codebase's existing
`ModelColumns` Protocol style in `app.repositories.base`) so a second
provider (e.g. Viva Wallet, for iris) can be added later without
touching `BillingService`. Deliberately minimal — five methods, not a
speculative full CRUD surface over the provider's entire API.

Only Stripe Checkout Session + Billing Portal URLs are ever used — no
card data transits our own API (PCI SAQ-A). Every implementation of
this Protocol must preserve that invariant.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from decimal import Decimal
from typing import Any
from typing import Protocol


@dataclass
class CouponApplication:
    """Normalized discount to apply at checkout, provider-agnostic.

    Built from our `Coupon` row by the caller (`BillingService`), so
    `PaymentProvider` implementations never need to depend on our ORM
    models — only this plain-data shape.

    Only covers `percent`/`fixed_amount` discounts. A `Coupon` with
    `discount_type=FREE` (a free-period trial extension) never becomes
    a `CouponApplication` — `BillingService.start_checkout` maps it
    directly onto `create_checkout_session`'s `trial_period_days`
    instead, since Stripe coupons have no "waive N days" concept.
    """

    code: str
    discount_type: str  # "percent" | "fixed_amount"
    percent_off: int | None = None
    amount_off_cents: int | None = None
    currency: str | None = None
    duration: str = "once"  # "once" | "repeating" | "forever"
    duration_in_months: int | None = None
    stripe_coupon_id: str | None = None


@dataclass
class CheckoutSession:
    """A created checkout session, normalized across providers."""

    id: str
    url: str
    stripe_customer_id: str | None = None
    # Set when a `CouponApplication` with no `stripe_coupon_id` was
    # passed in and the provider had to lazily create its own coupon
    # object. The caller (`BillingService`, which holds the DB session)
    # is responsible for caching this back onto the `Coupon` row —
    # `PaymentProvider` implementations never touch our database.
    created_stripe_coupon_id: str | None = None


@dataclass
class ProviderInvoice:
    """An invoice as reported by the provider, normalized across providers.

    VAT fields (`vat_rate`, `vat_amount_cents`, `customer_country`,
    `reverse_charge`) come directly from the provider's tax engine
    (Stripe Tax) — nothing in this codebase calculates VAT or validates
    VAT IDs itself.
    """

    id: str
    status: str
    amount_due_cents: int
    amount_paid_cents: int
    currency: str
    vat_rate: Decimal | None
    vat_amount_cents: int
    vat_id: str | None
    customer_country: str | None
    reverse_charge: bool
    billing_name: str | None
    billing_address: dict[str, Any] = field(default_factory=dict)
    hosted_invoice_url: str | None = None
    line_items: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ProviderEvent:
    """A verified webhook event, normalized across providers."""

    id: str
    type: str
    data: dict[str, Any]


class PaymentProviderError(Exception):
    """Raised when a provider call fails at the API boundary.

    Callers (`BillingService`, API routes) catch this instead of a
    provider-specific exception (e.g. `stripe.StripeError`), so the
    service/HTTP layers never need to import a specific provider's SDK
    to handle its failures.
    """


class PaymentProvider(Protocol):
    """Provider-agnostic interface for subscription billing operations."""

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
        """Create a hosted checkout session for a plan subscription.

        `idempotency_key`, when given, must be stable across retries of
        the *same* logical attempt (e.g. derived from the subscription
        ID) so a network retry or a double-submitted request can't
        create a second Checkout Session for the same attempt.
        """
        ...

    async def create_billing_portal_session(
        self,
        *,
        stripe_customer_id: str,
        return_url: str,
        idempotency_key: str | None = None,
    ) -> str:
        """Create a hosted billing portal session, returning its URL."""
        ...

    async def cancel_subscription(
        self,
        *,
        stripe_subscription_id: str,
        at_period_end: bool = True,
        idempotency_key: str | None = None,
    ) -> None:
        """Cancel a subscription, immediately or at period end."""
        ...

    async def retrieve_invoice(self, *, stripe_invoice_id: str) -> ProviderInvoice:
        """Fetch an invoice's normalized details from the provider."""
        ...

    def verify_webhook_signature(
        self, *, payload: bytes, signature_header: str
    ) -> ProviderEvent:
        """Verify a webhook payload's signature and return the parsed event.

        Raises whatever provider-specific exception signals an invalid
        signature — callers translate that into an HTTP 400.
        """
        ...
