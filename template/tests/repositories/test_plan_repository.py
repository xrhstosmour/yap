"""Unit tests for PlanRepository, especially the tenant-filter bypass.

`Plan` is a global catalog (`tenant_id` always `NULL`). This is the
regression test for that divergence — without it, a future refactor
could silently reintroduce `BaseRepository._apply_tenant_filter`'s
default behavior and make plans invisible from inside an active tenant
context (`WHERE tenant_id = :tenant_id` against an always-`NULL` column
matches zero rows).
"""

from __future__ import annotations

import pytest

from app.core.tenant import tenant_context
from app.models.plan import BillingInterval
from app.models.tenant import Tenant
from app.repositories.plan_repository import PlanRepository


class TestPlanRepositoryTenantBypass:
    @pytest.fixture
    def anyio_backend(self) -> str:
        return "asyncio"

    async def test_get_visible_from_inside_active_tenant_context(self, session) -> None:
        """A `Plan` (tenant_id=NULL) must remain visible even when a tenant
        context is active — the whole point of the no-op override."""
        tenant = Tenant(name="Plan Test Org", slug="plan-test-org")
        session.add(tenant)
        await session.commit()

        repository = PlanRepository(session)
        plan = await repository.create(
            {
                "name": "Pro",
                "amount_cents": 2900,
                "billing_interval": BillingInterval.MONTH,
            }
        )
        await session.commit()

        with tenant_context(tenant.id):
            fetched = await repository.get(plan.id)

        assert fetched is not None
        assert fetched.id == plan.id
        assert fetched.tenant_id is None

    async def test_list_visible_from_inside_active_tenant_context(self, session) -> None:
        tenant = Tenant(name="Plan Test Org 2", slug="plan-test-org-2")
        session.add(tenant)
        await session.commit()

        repository = PlanRepository(session)
        await repository.create(
            {
                "name": "Starter",
                "amount_cents": 900,
                "billing_interval": BillingInterval.MONTH,
            }
        )
        await session.commit()

        with tenant_context(tenant.id):
            plans, total = await repository.list()

        assert total >= 1
        assert any(p.name == "Starter" for p in plans)

    async def test_get_by_name(self, session) -> None:
        repository = PlanRepository(session)
        await repository.create(
            {
                "name": "Enterprise",
                "amount_cents": 9900,
                "billing_interval": BillingInterval.YEAR,
            }
        )
        await session.commit()

        found = await repository.get_by_name("Enterprise")
        assert found is not None
        assert found.name == "Enterprise"

        missing = await repository.get_by_name("Nonexistent")
        assert missing is None
