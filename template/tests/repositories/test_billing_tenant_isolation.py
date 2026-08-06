"""Standard tenant-isolation tests for the billing repositories.

`Subscription`, `Invoice`, `Payment`, and `PaymentMethod` are ordinary
tenant-scoped models (unlike `Plan`/`Coupon`/`InvoiceSequence`, which
deliberately bypass tenant filtering — see `tests/repositories/
test_plan_repository.py` and `test_coupon_repository.py`). These confirm
`BaseRepository._apply_tenant_filter`'s default behavior still applies
to them correctly.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.tenant import tenant_context
from app.models.invoice import InvoiceStatus
from app.models.payment import PaymentMethodType
from app.models.payment import PaymentStatus
from app.models.subscription import SubscriptionStatus
from app.models.tenant import Tenant
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.payment_repository import PaymentMethodRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.subscription_repository import SubscriptionRepository


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _two_tenants(session) -> tuple[Tenant, Tenant]:
    tenant_a = Tenant(name="Isolation Org A", slug="isolation-org-a")
    tenant_b = Tenant(name="Isolation Org B", slug="isolation-org-b")
    session.add(tenant_a)
    session.add(tenant_b)
    await session.commit()
    return tenant_a, tenant_b


class TestSubscriptionRepositoryTenantIsolation:
    async def test_list_does_not_leak_across_tenants(self, session) -> None:
        tenant_a, tenant_b = await _two_tenants(session)
        repository = SubscriptionRepository(session)

        with tenant_context(tenant_a.id):
            await repository.create({"status": SubscriptionStatus.TRIALING})
        with tenant_context(tenant_b.id):
            await repository.create({"status": SubscriptionStatus.TRIALING})

        with tenant_context(tenant_a.id):
            subscriptions, total = await repository.list()

        assert total == 1
        assert all(s.tenant_id == tenant_a.id for s in subscriptions)

    async def test_get_active_for_tenant_does_not_leak(self, session) -> None:
        tenant_a, tenant_b = await _two_tenants(session)
        repository = SubscriptionRepository(session)

        with tenant_context(tenant_b.id):
            await repository.create({"status": SubscriptionStatus.ACTIVE})

        result = await repository.get_active_for_tenant(tenant_a.id)
        assert result is None


class TestInvoiceRepositoryTenantIsolation:
    async def test_list_for_tenant_does_not_leak(self, session) -> None:
        tenant_a, tenant_b = await _two_tenants(session)
        repository = InvoiceRepository(session)

        await repository.issue_invoice(
            "iso-2091",
            {
                "tenant_id": tenant_a.id,
                "status": InvoiceStatus.PAID,
                "issue_date": date.today(),
                "amount_due_cents": 100,
            },
        )
        await repository.issue_invoice(
            "iso-2091",
            {
                "tenant_id": tenant_b.id,
                "status": InvoiceStatus.PAID,
                "issue_date": date.today(),
                "amount_due_cents": 200,
            },
        )

        invoices, total = await repository.list_for_tenant(tenant_a.id)
        assert total == 1
        assert invoices[0].tenant_id == tenant_a.id


class TestPaymentRepositoryTenantIsolation:
    async def test_list_for_tenant_does_not_leak(self, session) -> None:
        tenant_a, tenant_b = await _two_tenants(session)
        repository = PaymentRepository(session)

        await repository.create(
            {
                "tenant_id": tenant_a.id,
                "amount_cents": 100,
                "status": PaymentStatus.SUCCEEDED,
            }
        )
        await repository.create(
            {
                "tenant_id": tenant_b.id,
                "amount_cents": 200,
                "status": PaymentStatus.SUCCEEDED,
            }
        )

        payments, total = await repository.list_for_tenant(tenant_a.id)
        assert total == 1
        assert payments[0].tenant_id == tenant_a.id


class TestPaymentMethodRepositoryTenantIsolation:
    async def test_list_for_tenant_does_not_leak(self, session) -> None:
        tenant_a, tenant_b = await _two_tenants(session)
        repository = PaymentMethodRepository(session)

        await repository.create(
            {
                "tenant_id": tenant_a.id,
                "stripe_payment_method_id": "pm_iso_a",
                "type": PaymentMethodType.CARD,
            }
        )
        await repository.create(
            {
                "tenant_id": tenant_b.id,
                "stripe_payment_method_id": "pm_iso_b",
                "type": PaymentMethodType.CARD,
            }
        )

        methods = await repository.list_for_tenant(tenant_a.id)
        assert len(methods) == 1
        assert methods[0].tenant_id == tenant_a.id
