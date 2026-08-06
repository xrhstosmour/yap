"""Unit tests for CouponRepository, especially the tenant-filter bypass.

`Coupon` is a global catalog (`tenant_id` always `NULL`). See
`tests/repositories/test_plan_repository.py` for why this regression
test matters.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.tenant import tenant_context
from app.models.coupon import CouponDiscountType
from app.models.coupon import CouponDuration
from app.models.tenant import Tenant
from app.repositories.coupon_repository import CouponRepository
from app.services.billing_service import BillingService
from app.services.billing_service import CouponNotApplicableError


class TestCouponRepositoryTenantBypass:
    @pytest.fixture
    def anyio_backend(self) -> str:
        return "asyncio"

    async def test_get_visible_from_inside_active_tenant_context(self, session) -> None:
        tenant = Tenant(name="Coupon Test Org", slug="coupon-test-org")
        session.add(tenant)
        await session.commit()

        repository = CouponRepository(session)
        coupon = await repository.create(
            {
                "code": "WELCOME10",
                "discount_type": CouponDiscountType.PERCENT,
                "percent_off": 10,
                "duration": CouponDuration.ONCE,
            }
        )
        await session.commit()

        with tenant_context(tenant.id):
            fetched = await repository.get(coupon.id)

        assert fetched is not None
        assert fetched.tenant_id is None

    async def test_get_by_code_normalizes_case(self, session) -> None:
        repository = CouponRepository(session)
        await repository.create(
            {
                "code": "SUMMER20",
                "discount_type": CouponDiscountType.PERCENT,
                "percent_off": 20,
                "duration": CouponDuration.ONCE,
            }
        )
        await session.commit()

        found = await repository.get_by_code("summer20")
        assert found is not None
        assert found.code == "SUMMER20"

    async def test_increment_redemption_count(self, session) -> None:
        repository = CouponRepository(session)
        coupon = await repository.create(
            {
                "code": "COUNTME",
                "discount_type": CouponDiscountType.FREE,
                "duration": CouponDuration.ONCE,
            }
        )
        await session.commit()

        await repository.increment_redemption_count(coupon.id)
        refreshed = await repository.get(coupon.id)
        assert refreshed is not None
        assert refreshed.redemption_count == 1

    async def test_allowed_plan_ids_round_trips_and_validates(self, session) -> None:
        """`allowed_plan_ids`/`allowed_tenant_ids` must survive a real
        JSON round-trip and still correctly restrict `validate_coupon`.

        Regression test: the columns were typed `list[UUID]` over a
        plain `JSON` column (which can't serialize `UUID` at all) and
        compared with `UUID in list[UUID]`, so a real DB-backed coupon's
        allow-list rejected every plan/tenant, restricted or not.
        """
        allowed_plan_id = uuid4()
        other_plan_id = uuid4()
        tenant_id = uuid4()

        repository = CouponRepository(session)
        coupon = await repository.create(
            {
                "code": "PLANLOCK",
                "discount_type": CouponDiscountType.PERCENT,
                "percent_off": 15,
                "duration": CouponDuration.ONCE,
                "allowed_plan_ids": [str(allowed_plan_id)],
            }
        )
        await session.commit()

        refreshed = await repository.get(coupon.id)
        assert refreshed is not None
        assert refreshed.allowed_plan_ids == [str(allowed_plan_id)]

        billing_service = BillingService(session, None)
        applicable = await billing_service.validate_coupon(
            "PLANLOCK", tenant_id, allowed_plan_id
        )
        assert applicable.code == "PLANLOCK"

        with pytest.raises(CouponNotApplicableError):
            await billing_service.validate_coupon("PLANLOCK", tenant_id, other_plan_id)
