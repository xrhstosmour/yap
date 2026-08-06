"""Integration tests for `SubscriptionService.transition` against real Postgres.

Regression coverage for a real identity-map bug: `BaseRepository.update()`
mutates the ORM object in place (same session, same identity-mapped
instance) rather than returning a distinct object, so reading a field
off the *original* reference after the write already reflects the new
value. A mocked-repository unit test cannot catch this — `AsyncMock`
returns a distinct object, never mutates its input.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.audit_log import AuditAction
from app.models.subscription import Subscription
from app.models.subscription import SubscriptionStatus
from app.models.tenant import Tenant
from app.repositories.audit_repository import AuditLogRepository
from app.services.billing_service import SubscriptionService


class TestTransitionAuditTrailAgainstRealDatabase:
    @pytest.fixture
    def anyio_backend(self) -> str:
        return "asyncio"

    async def test_audit_log_records_the_original_from_status(self, session) -> None:
        tenant = Tenant(name="Transition Audit Org", slug="transition-audit-org")
        session.add(tenant)
        await session.commit()

        subscription = Subscription(
            tenant_id=tenant.id, status=SubscriptionStatus.TRIALING
        )
        session.add(subscription)
        await session.commit()

        audit_repository = AuditLogRepository(session)
        service = SubscriptionService(session, audit_repository)

        updated = await service.transition(
            subscription.id, SubscriptionStatus.ACTIVE, source="test"
        )
        assert updated.status == SubscriptionStatus.ACTIVE

        logs, _ = await audit_repository.get_by_resource(
            resource_type="subscription", resource_id=str(subscription.id)
        )
        assert len(logs) == 1
        assert logs[0].action == AuditAction.SUBSCRIPTION_STATUS_CHANGED.value
        assert logs[0].extra_data["from"] == "trialing"
        assert logs[0].extra_data["to"] == "active"


class TestOneNonTerminalSubscriptionPerTenantConstraint:
    """DB-level regression coverage for the partial unique index.

    A mocked-repository unit test can't catch a missing/broken database
    constraint — only a real Postgres round-trip can. Terminal-status
    rows (`expired`/`canceled`) are exempt so a resubscribing tenant can
    accumulate subscription history without conflicting.
    """

    @pytest.fixture
    def anyio_backend(self) -> str:
        return "asyncio"

    async def test_second_non_terminal_subscription_is_rejected(self, session) -> None:
        tenant = Tenant(name="One Sub Org", slug="one-sub-org")
        session.add(tenant)
        await session.commit()

        session.add(
            Subscription(tenant_id=tenant.id, status=SubscriptionStatus.TRIALING)
        )
        await session.commit()

        session.add(Subscription(tenant_id=tenant.id, status=SubscriptionStatus.ACTIVE))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async def test_terminal_rows_do_not_conflict(self, session) -> None:
        tenant = Tenant(name="Resubscribe Org", slug="resubscribe-org")
        session.add(tenant)
        await session.commit()

        session.add(
            Subscription(tenant_id=tenant.id, status=SubscriptionStatus.EXPIRED)
        )
        await session.commit()

        session.add(
            Subscription(tenant_id=tenant.id, status=SubscriptionStatus.CANCELED)
        )
        await session.commit()

        session.add(
            Subscription(tenant_id=tenant.id, status=SubscriptionStatus.TRIALING)
        )
        await session.commit()
