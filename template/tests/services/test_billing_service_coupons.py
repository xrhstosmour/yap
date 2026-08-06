"""Tests for `BillingService.validate_coupon`'s edge cases."""

from __future__ import annotations

import asyncio
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.models.coupon import Coupon
from app.models.coupon import CouponDiscountType
from app.models.coupon import CouponDuration
from app.services.billing_service import BillingService
from app.services.billing_service import CouponAlreadyRedeemedError
from app.services.billing_service import CouponExhaustedError
from app.services.billing_service import CouponExpiredError
from app.services.billing_service import CouponNotApplicableError
from app.services.billing_service import CouponNotFoundError

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
OTHER_TENANT_ID = UUID("00000000-0000-0000-0000-000000000099")
PLAN_ID = UUID("00000000-0000-0000-0000-000000000002")
OTHER_PLAN_ID = UUID("00000000-0000-0000-0000-000000000098")
COUPON_ID = UUID("00000000-0000-0000-0000-000000000003")


def _coupon(**overrides) -> Coupon:  # noqa: ANN003
    defaults = {
        "id": COUPON_ID,
        "code": "SAVE10",
        "discount_type": CouponDiscountType.PERCENT,
        "percent_off": 10,
        "duration": CouponDuration.ONCE,
        "is_active": True,
        "max_redemptions": None,
        "redemption_count": 0,
        "valid_from": None,
        "valid_until": None,
        "allowed_plan_ids": None,
        "allowed_tenant_ids": None,
    }
    defaults.update(overrides)
    return Coupon(**defaults)


@pytest.fixture
def service() -> BillingService:
    service = BillingService(MagicMock(), MagicMock())
    service.coupon_repository = MagicMock()
    service.coupon_redemption_repository = MagicMock()
    service.coupon_redemption_repository.get_active_for_tenant = AsyncMock(
        return_value=None
    )
    return service


class TestValidateCoupon:
    def test_valid_coupon_passes(self, service: BillingService) -> None:
        service.coupon_repository.get_by_code = AsyncMock(return_value=_coupon())

        result = asyncio.run(service.validate_coupon("SAVE10", TENANT_ID, PLAN_ID))
        assert result.code == "SAVE10"

    def test_unknown_code_raises_not_found(self, service: BillingService) -> None:
        service.coupon_repository.get_by_code = AsyncMock(return_value=None)

        with pytest.raises(CouponNotFoundError):
            asyncio.run(service.validate_coupon("NOPE", TENANT_ID, PLAN_ID))

    def test_inactive_coupon_raises_not_found(self, service: BillingService) -> None:
        service.coupon_repository.get_by_code = AsyncMock(
            return_value=_coupon(is_active=False)
        )

        with pytest.raises(CouponNotFoundError):
            asyncio.run(service.validate_coupon("SAVE10", TENANT_ID, PLAN_ID))

    def test_not_yet_valid_raises_expired(self, service: BillingService) -> None:
        future = datetime.now(UTC) + timedelta(days=1)
        service.coupon_repository.get_by_code = AsyncMock(
            return_value=_coupon(valid_from=future)
        )

        with pytest.raises(CouponExpiredError):
            asyncio.run(service.validate_coupon("SAVE10", TENANT_ID, PLAN_ID))

    def test_past_valid_until_raises_expired(self, service: BillingService) -> None:
        past = datetime.now(UTC) - timedelta(days=1)
        service.coupon_repository.get_by_code = AsyncMock(
            return_value=_coupon(valid_until=past)
        )

        with pytest.raises(CouponExpiredError):
            asyncio.run(service.validate_coupon("SAVE10", TENANT_ID, PLAN_ID))

    def test_exhausted_raises(self, service: BillingService) -> None:
        service.coupon_repository.get_by_code = AsyncMock(
            return_value=_coupon(max_redemptions=5, redemption_count=5)
        )

        with pytest.raises(CouponExhaustedError):
            asyncio.run(service.validate_coupon("SAVE10", TENANT_ID, PLAN_ID))

    def test_wrong_plan_raises_not_applicable(self, service: BillingService) -> None:
        service.coupon_repository.get_by_code = AsyncMock(
            return_value=_coupon(allowed_plan_ids=[OTHER_PLAN_ID])
        )

        with pytest.raises(CouponNotApplicableError):
            asyncio.run(service.validate_coupon("SAVE10", TENANT_ID, PLAN_ID))

    def test_wrong_tenant_raises_not_applicable(self, service: BillingService) -> None:
        service.coupon_repository.get_by_code = AsyncMock(
            return_value=_coupon(allowed_tenant_ids=[OTHER_TENANT_ID])
        )

        with pytest.raises(CouponNotApplicableError):
            asyncio.run(service.validate_coupon("SAVE10", TENANT_ID, PLAN_ID))

    def test_allowed_plan_and_tenant_pass(self, service: BillingService) -> None:
        service.coupon_repository.get_by_code = AsyncMock(
            return_value=_coupon(
                allowed_plan_ids=[PLAN_ID], allowed_tenant_ids=[TENANT_ID]
            )
        )

        result = asyncio.run(service.validate_coupon("SAVE10", TENANT_ID, PLAN_ID))
        assert result.code == "SAVE10"

    def test_already_redeemed_raises(self, service: BillingService) -> None:
        service.coupon_repository.get_by_code = AsyncMock(return_value=_coupon())
        service.coupon_redemption_repository.get_active_for_tenant = AsyncMock(
            return_value=MagicMock()
        )

        with pytest.raises(CouponAlreadyRedeemedError):
            asyncio.run(service.validate_coupon("SAVE10", TENANT_ID, PLAN_ID))
