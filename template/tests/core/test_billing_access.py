"""Tests for `get_active_billing_status`."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.billing_access import get_active_billing_status
from app.models.subscription import Subscription
from app.models.subscription import SubscriptionStatus
from app.models.tenant import Tenant
from app.models.user import User


class TestGetActiveBillingStatus:
    @pytest.fixture
    def anyio_backend(self) -> str:
        return "asyncio"

    async def _tenant_user(self, session, status: SubscriptionStatus | None) -> User:
        tenant = Tenant(name="Billing Access Org", slug=f"billing-access-{status}")
        session.add(tenant)
        await session.commit()

        if status is not None:
            subscription = Subscription(tenant_id=tenant.id, status=status)
            session.add(subscription)
            await session.commit()

        user = User(
            email=f"user-{status}@example.com",
            hashed_password="x",
            tenant_id=tenant.id,
        )
        session.add(user)
        await session.commit()
        return user

    async def test_active_subscription_passes(self, session) -> None:
        user = await self._tenant_user(session, SubscriptionStatus.ACTIVE)
        result = await get_active_billing_status(user, session)
        assert result.subscription_status == SubscriptionStatus.ACTIVE
        assert result.grace_period_active is False

    async def test_trialing_subscription_passes(self, session) -> None:
        user = await self._tenant_user(session, SubscriptionStatus.TRIALING)
        result = await get_active_billing_status(user, session)
        assert result.subscription_status == SubscriptionStatus.TRIALING

    async def test_grace_period_subscription_passes_with_flag_set(
        self, session
    ) -> None:
        user = await self._tenant_user(session, SubscriptionStatus.GRACE_PERIOD)
        result = await get_active_billing_status(user, session)
        assert result.grace_period_active is True

    async def test_expired_subscription_raises_402(self, session) -> None:
        user = await self._tenant_user(session, SubscriptionStatus.EXPIRED)
        with pytest.raises(HTTPException) as excinfo:
            await get_active_billing_status(user, session)
        assert excinfo.value.status_code == 402

    async def test_no_subscription_row_passes_through(self, session) -> None:
        user = await self._tenant_user(session, None)
        result = await get_active_billing_status(user, session)
        assert result.subscription_status is None

    async def test_no_tenant_passes_through(self, session) -> None:
        user = User(email="no-tenant@example.com", hashed_password="x")
        session.add(user)
        await session.commit()

        result = await get_active_billing_status(user, session)
        assert result.subscription_status is None
