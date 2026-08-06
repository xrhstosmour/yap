"""Tests for `BillingService.start_checkout`/`create_portal_session`.

These exercise the `PaymentProvider` boundary with a mock — nothing
here makes a real Stripe API call, but unlike the rest of the suite,
these specifically assert *what* gets passed to the provider, since
that's exactly where the checkout-session-construction bugs live (a
missing param here means Stripe Tax silently never runs, or a coupon
silently does nothing).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.core.payment_provider import CheckoutSession
from app.models.coupon import Coupon
from app.models.coupon import CouponDiscountType
from app.models.coupon import CouponDuration
from app.models.coupon import CouponRedemption
from app.models.plan import BillingInterval
from app.models.plan import Plan
from app.models.subscription import Subscription
from app.models.subscription import SubscriptionStatus
from app.services.billing_service import BillingService
from app.services.billing_service import NoActiveSubscriptionError

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
PLAN_ID = UUID("00000000-0000-0000-0000-000000000002")
SUBSCRIPTION_ID = UUID("00000000-0000-0000-0000-000000000003")
COUPON_ID = UUID("00000000-0000-0000-0000-000000000004")
USER_ID = UUID("00000000-0000-0000-0000-000000000005")
REDEMPTION_ID = UUID("00000000-0000-0000-0000-000000000006")


def _plan(**overrides) -> Plan:
    defaults = {
        "id": PLAN_ID,
        "name": "Pro",
        "stripe_price_id": "price_test123",
        "amount_cents": 2900,
        "currency": "eur",
        "billing_interval": BillingInterval.MONTH,
        "is_active": True,
    }
    defaults.update(overrides)
    return Plan(**defaults)


def _subscription(**overrides) -> Subscription:
    defaults = {
        "id": SUBSCRIPTION_ID,
        "tenant_id": TENANT_ID,
        "plan_id": None,
        "status": SubscriptionStatus.TRIALING,
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
    }
    defaults.update(overrides)
    return Subscription(**defaults)


def _coupon(**overrides) -> Coupon:
    defaults = {
        "id": COUPON_ID,
        "code": "FREE14",
        "discount_type": CouponDiscountType.FREE,
        "free_days": 14,
        "duration": CouponDuration.ONCE,
        "is_active": True,
        "max_redemptions": None,
        "redemption_count": 0,
        "valid_from": None,
        "valid_until": None,
        "allowed_plan_ids": None,
        "allowed_tenant_ids": None,
        "stripe_coupon_id": None,
    }
    defaults.update(overrides)
    return Coupon(**defaults)


@pytest.fixture
def payment_provider() -> AsyncMock:
    provider = AsyncMock()
    provider.create_checkout_session.return_value = CheckoutSession(
        id="cs_test123",
        url="https://checkout.stripe.com/cs_test123",
        stripe_customer_id="cus_test123",
    )
    provider.create_billing_portal_session.return_value = (
        "https://billing.stripe.com/session_test123"
    )
    return provider


@pytest.fixture
def service(payment_provider: AsyncMock) -> BillingService:
    service = BillingService(MagicMock(), payment_provider)
    service.plan_repository = MagicMock()
    service.subscription_repository = MagicMock()
    service.coupon_repository = MagicMock()
    service.coupon_redemption_repository = MagicMock()
    return service


class TestStartCheckout:
    def test_no_coupon_passes_no_trial_and_no_discount(
        self, service: BillingService, payment_provider: AsyncMock
    ) -> None:
        service.plan_repository.get = AsyncMock(return_value=_plan())
        service.subscription_repository.get_active_for_tenant = AsyncMock(
            return_value=_subscription()
        )

        asyncio.run(
            service.start_checkout(
                tenant_id=TENANT_ID,
                plan_id=PLAN_ID,
                user_id=USER_ID,
                user_email="owner@example.com",
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
            )
        )

        payment_provider.create_checkout_session.assert_awaited_once()
        _, kwargs = payment_provider.create_checkout_session.await_args
        assert kwargs["price_id"] == "price_test123"
        assert kwargs["trial_period_days"] is None
        assert kwargs["coupon"] is None

    def test_free_days_coupon_becomes_trial_period_not_a_discount(
        self, service: BillingService, payment_provider: AsyncMock
    ) -> None:
        """`discount_type=FREE` must map to `trial_period_days`.

        Regression test: this used to build a `CouponApplication` for
        `FREE` coupons too, which `StripeProvider` turned into a
        real-but-worthless `amount_off=0` Stripe coupon — the customer
        was charged immediately and the free days were never granted.
        """
        service.plan_repository.get = AsyncMock(return_value=_plan())
        service.subscription_repository.get_active_for_tenant = AsyncMock(
            return_value=_subscription()
        )
        service.coupon_repository.get_by_code = AsyncMock(return_value=_coupon())
        service.coupon_repository.get_for_update = AsyncMock(return_value=_coupon())
        service.coupon_repository.increment_redemption_count = AsyncMock()
        service.coupon_redemption_repository.get_active_for_tenant = AsyncMock(
            return_value=None
        )
        service.coupon_redemption_repository.create = AsyncMock(
            return_value=CouponRedemption(
                id=REDEMPTION_ID,
                tenant_id=TENANT_ID,
                coupon_id=COUPON_ID,
                redeemed_by_user_id=USER_ID,
            )
        )
        service._log_audit = AsyncMock()

        asyncio.run(
            service.start_checkout(
                tenant_id=TENANT_ID,
                plan_id=PLAN_ID,
                user_id=USER_ID,
                user_email="owner@example.com",
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
                coupon_code="FREE14",
            )
        )

        payment_provider.create_checkout_session.assert_awaited_once()
        _, kwargs = payment_provider.create_checkout_session.await_args
        assert kwargs["trial_period_days"] == 14
        assert kwargs["coupon"] is None

    def test_percent_coupon_becomes_discount_not_a_trial(
        self, service: BillingService, payment_provider: AsyncMock
    ) -> None:
        service.plan_repository.get = AsyncMock(return_value=_plan())
        service.subscription_repository.get_active_for_tenant = AsyncMock(
            return_value=_subscription()
        )
        percent_coupon = _coupon(
            code="SAVE20",
            discount_type=CouponDiscountType.PERCENT,
            percent_off=20,
            free_days=0,
        )
        service.coupon_repository.get_by_code = AsyncMock(return_value=percent_coupon)
        service.coupon_repository.get_for_update = AsyncMock(
            return_value=percent_coupon
        )
        service.coupon_repository.increment_redemption_count = AsyncMock()
        service.coupon_redemption_repository.get_active_for_tenant = AsyncMock(
            return_value=None
        )
        service.coupon_redemption_repository.create = AsyncMock(
            return_value=CouponRedemption(
                id=REDEMPTION_ID,
                tenant_id=TENANT_ID,
                coupon_id=COUPON_ID,
                redeemed_by_user_id=USER_ID,
            )
        )
        service._log_audit = AsyncMock()

        asyncio.run(
            service.start_checkout(
                tenant_id=TENANT_ID,
                plan_id=PLAN_ID,
                user_id=USER_ID,
                user_email="owner@example.com",
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
                coupon_code="SAVE20",
            )
        )

        payment_provider.create_checkout_session.assert_awaited_once()
        _, kwargs = payment_provider.create_checkout_session.await_args
        assert kwargs["trial_period_days"] is None
        assert kwargs["coupon"] is not None
        assert kwargs["coupon"].discount_type == "percent"
        assert kwargs["coupon"].percent_off == 20

    def test_no_subscription_row_creates_fresh_subscription(
        self, service: BillingService, payment_provider: AsyncMock
    ) -> None:
        """A tenant with no non-terminal subscription (first-ever checkout
        racing ahead of provisioning, or resubscribing after
        `expired`/`canceled`) gets a brand-new row instead of a 404.

        Regression test: `start_checkout` used to raise
        `NoActiveSubscriptionError` here, permanently locking out any
        tenant whose subscription had lapsed — `/checkout` is supposed
        to stay reachable specifically so a lapsed tenant can reactivate.
        """
        service.plan_repository.get = AsyncMock(return_value=_plan())
        service.subscription_repository.get_active_for_tenant = AsyncMock(
            return_value=None
        )
        service.subscription_repository.create = AsyncMock(
            return_value=_subscription(stripe_customer_id=None)
        )
        service._log_audit = AsyncMock()

        asyncio.run(
            service.start_checkout(
                tenant_id=TENANT_ID,
                plan_id=PLAN_ID,
                user_id=USER_ID,
                user_email="owner@example.com",
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
            )
        )

        service.subscription_repository.create.assert_awaited_once()
        call_kwargs = service.subscription_repository.create.call_args[0][0]
        assert call_kwargs["tenant_id"] == TENANT_ID
        assert call_kwargs["plan_id"] == PLAN_ID
        assert call_kwargs["status"] == SubscriptionStatus.TRIALING
        payment_provider.create_checkout_session.assert_awaited_once()

    def test_fresh_subscription_row_is_bounded_not_indefinite(
        self, service: BillingService, payment_provider: AsyncMock
    ) -> None:
        """The fresh row `start_checkout` creates when a tenant has no
        non-terminal subscription must have a bounded `trial_ends_at`/
        `grace_period_ends_at`, not `None`.

        Regression test: this used to leave both unset so the lifecycle
        sweep (`list_due_for_sweep`, which requires `trial_ends_at IS
        NOT NULL` for `trialing` rows) could never reclaim an abandoned
        checkout attempt — an expired tenant could hit `/checkout`
        without ever paying and keep full product access indefinitely.
        """
        service.plan_repository.get = AsyncMock(return_value=_plan())
        service.subscription_repository.get_active_for_tenant = AsyncMock(
            return_value=None
        )
        service.subscription_repository.create = AsyncMock(
            return_value=_subscription(stripe_customer_id=None)
        )
        service._log_audit = AsyncMock()

        asyncio.run(
            service.start_checkout(
                tenant_id=TENANT_ID,
                plan_id=PLAN_ID,
                user_id=USER_ID,
                user_email="owner@example.com",
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
            )
        )

        call_kwargs = service.subscription_repository.create.call_args[0][0]
        assert call_kwargs["trial_ends_at"] is not None
        assert call_kwargs["grace_period_ends_at"] is not None
        assert call_kwargs["grace_period_ends_at"] > call_kwargs["trial_ends_at"]


class TestCreatePortalSession:
    def test_returns_provider_url(
        self, service: BillingService, payment_provider: AsyncMock
    ) -> None:
        service.subscription_repository.get_active_for_tenant = AsyncMock(
            return_value=_subscription(stripe_customer_id="cus_test123")
        )

        url = asyncio.run(
            service.create_portal_session(TENANT_ID, "https://example.com/return")
        )

        assert url == "https://billing.stripe.com/session_test123"
        payment_provider.create_billing_portal_session.assert_awaited_once()
        _, kwargs = payment_provider.create_billing_portal_session.await_args
        assert kwargs["stripe_customer_id"] == "cus_test123"
        assert kwargs["return_url"] == "https://example.com/return"
        assert kwargs["idempotency_key"]

    def test_no_stripe_customer_id_raises(
        self, service: BillingService, payment_provider: AsyncMock
    ) -> None:
        service.subscription_repository.get_active_for_tenant = AsyncMock(
            return_value=_subscription(stripe_customer_id=None)
        )

        with pytest.raises(NoActiveSubscriptionError):
            asyncio.run(
                service.create_portal_session(TENANT_ID, "https://example.com/return")
            )
        payment_provider.create_billing_portal_session.assert_not_awaited()
