"""Pydantic schemas for billing API requests and responses."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field
from pydantic import field_validator

from app.core.settings import settings
from app.schemas.base import BaseSchema
from app.schemas.base import PaginatedResponse


def _validate_redirect_origin(value: str) -> str:
    """Reject redirect URLs whose origin isn't in `settings.all_cors_origins`.

    Stripe redirects the tenant's browser to these URLs verbatim after
    checkout/portal completion — without this check, checkout/portal
    become an open redirect to any host the caller supplies.
    """
    parsed = urlsplit(value)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in settings.all_cors_origins:
        raise ValueError(f"URL origin '{origin}' is not an allowed redirect origin")
    return value


class CheckoutRequest(BaseSchema):
    """Request to start a Stripe Checkout session for a plan subscription."""

    plan_id: UUID
    success_url: str = Field(max_length=2048)
    cancel_url: str = Field(max_length=2048)
    coupon_code: str | None = Field(default=None, max_length=64)

    _validate_success_url = field_validator("success_url")(_validate_redirect_origin)
    _validate_cancel_url = field_validator("cancel_url")(_validate_redirect_origin)


class CheckoutResponse(BaseSchema):
    """The created Checkout session, ready to redirect the tenant to."""

    checkout_url: str
    stripe_customer_id: str | None = None


class PortalRequest(BaseSchema):
    """Request to open the Stripe Billing Portal."""

    return_url: str = Field(max_length=2048)

    _validate_return_url = field_validator("return_url")(_validate_redirect_origin)


class PortalResponse(BaseSchema):
    """The created Billing Portal session URL."""

    portal_url: str


class CancelSubscriptionRequest(BaseSchema):
    """Request to cancel the tenant's subscription."""

    at_period_end: bool = Field(
        default=True,
        description="Cancel at the end of the current billing period "
        "(default) rather than immediately.",
    )


class CouponValidateRequest(BaseSchema):
    """Request to validate a coupon code before committing to checkout."""

    code: str = Field(min_length=1, max_length=64)
    plan_id: UUID


class CouponValidateResponse(BaseSchema):
    """Result of validating a coupon code."""

    valid: bool
    code: str
    discount_type: str | None = None
    percent_off: int | None = None
    amount_off_cents: int | None = None
    free_days: int | None = None
    reason: str | None = Field(
        default=None, description="Human-readable reason when `valid` is `False`."
    )


class SubscriptionResponse(BaseSchema):
    """The tenant's current subscription status."""

    id: UUID
    status: str
    plan_id: UUID | None = None
    trial_ends_at: datetime | None = None
    current_period_end: datetime | None = None
    grace_period_ends_at: datetime | None = None
    cancel_at_period_end: bool
    canceled_at: datetime | None = None
    created_at: datetime


class InvoiceResponse(BaseSchema):
    """A single invoice."""

    id: UUID
    invoice_number: str
    status: str
    issue_date: date
    amount_due_cents: int
    amount_paid_cents: int
    currency: str
    vat_rate: Decimal
    vat_amount_cents: int
    hosted_invoice_url: str | None = None
    paid_at: datetime | None = None


class InvoiceListResponse(PaginatedResponse[InvoiceResponse]):
    """Paginated list of invoices."""


class PaymentResponse(BaseSchema):
    """A single payment/charge attempt."""

    id: UUID
    amount_cents: int
    currency: str
    status: str
    failure_code: str | None = None
    failure_message: str | None = None
    refunded_amount_cents: int
    paid_at: datetime | None = None
    created_at: datetime


class PaymentListResponse(PaginatedResponse[PaymentResponse]):
    """Paginated list of payments."""


class PaymentMethodResponse(BaseSchema):
    """A saved payment method."""

    id: UUID
    type: str
    brand: str | None = None
    last_four: str | None = None
    exp_month: int | None = None
    exp_year: int | None = None
    is_default: bool
