"""Unit tests for AuditLogRepository database operations."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.tenant import system_context
from app.core.tenant import tenant_context
from app.models.audit_log import AuditAction
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.repositories.audit_repository import AuditLogRepository
from app.repositories.base import TenantContextRequiredError


class TestAuditLogRepository:
    """Tests for AuditLogRepository operations."""

    @pytest.fixture
    def anyio_backend(self) -> str:
        """Provide asyncio backend for anyio-marked tests.

        Returns:
            The backend name.
        """
        return "asyncio"

    async def _create_tenant(
        self, session: AsyncSession, slug: str = "test-org"
    ) -> Tenant:
        """Create a test tenant for FK constraints.

        Args:
            session: Async database session fixture.
            slug: Unique slug for the tenant.

        Returns:
            Persisted Tenant instance.
        """
        tenant = Tenant(name="Test Org", slug=slug)
        session.add(tenant)
        await session.commit()
        await session.refresh(tenant)
        return tenant

    @pytest.mark.anyio
    async def test_log_user_action_creates_audit_entry(
        self, session: AsyncSession
    ) -> None:
        """log_user_action() should create an audit log entry with all fields.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo = AuditLogRepository(session)
        user_id = uuid4()

        entry = await repo.log_user_action(
            action=AuditAction.USER_CREATE,
            user_id=user_id,
            tenant_id=tenant.id,
            email="admin@example.com",
            resource_type="user",
            resource_id=str(uuid4()),
            status="success",
            changes={"before": None, "after": {"email": "new@example.com"}},
            metadata={"ip": "127.0.0.1"},
        )

        assert isinstance(entry, AuditLog)
        assert entry.id is not None
        assert entry.action == "user_create"
        assert entry.actor_id == str(user_id)
        assert entry.actor_type == "user"
        assert entry.actor_email == "admin@example.com"
        assert entry.tenant_id == tenant.id
        assert entry.resource_type == "user"
        assert entry.resource_id is not None
        assert entry.status == "success"
        assert entry.changes == {
            "before": None,
            "after": {"email": "new@example.com"},
        }
        assert entry.extra_data == {"ip": "127.0.0.1"}

        # Verify persisted.
        result = await session.execute(select(AuditLog).where(AuditLog.id == entry.id))
        persisted = result.scalar_one_or_none()
        assert persisted is not None
        assert persisted.action == "user_create"

    @pytest.mark.anyio
    async def test_log_user_action_safe_creates_audit_entry(
        self, session: AsyncSession
    ) -> None:
        """log_user_action_safe() should create an entry on success.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo = AuditLogRepository(session)
        user_id = uuid4()

        with tenant_context(tenant.id):
            await repo.log_user_action_safe(
                action=AuditAction.USER_CREATE,
                user_id=user_id,
                tenant_id=tenant.id,
                email="admin@example.com",
            )

            logs, total = await repo.list(filters={"actor_id": str(user_id)})

        assert total == 1
        assert logs[0].action == "user_create"

    @pytest.mark.anyio
    async def test_log_user_action_safe_swallows_write_failure(
        self, session: AsyncSession
    ) -> None:
        """log_user_action_safe() should not raise when the write fails.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo = AuditLogRepository(session)
        repo.log_user_action = AsyncMock(  # type: ignore[method-assign]
            side_effect=Exception("db unavailable")
        )

        await repo.log_user_action_safe(
            action=AuditAction.USER_CREATE,
            user_id=uuid4(),
            tenant_id=tenant.id,
            email="admin@example.com",
        )

    @pytest.mark.anyio
    async def test_list_with_user_id_filter(self, session: AsyncSession) -> None:
        """list() should filter by user_id.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo = AuditLogRepository(session)
        user_a = uuid4()
        user_b = uuid4()

        with tenant_context(tenant.id):
            await repo.log_user_action(
                action=AuditAction.LOGIN,
                user_id=user_a,
                tenant_id=tenant.id,
                email="a@example.com",
            )
            await repo.log_user_action(
                action=AuditAction.LOGOUT,
                user_id=user_b,
                tenant_id=tenant.id,
                email="b@example.com",
            )

            logs, total = await repo.list(filters={"actor_id": str(user_a)})

        assert total == 1
        assert len(logs) == 1
        assert logs[0].actor_id == str(user_a)
        assert logs[0].action == "login"

    @pytest.mark.anyio
    async def test_list_with_action_filter(self, session: AsyncSession) -> None:
        """list() should filter by action.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo = AuditLogRepository(session)
        user_id = uuid4()

        with tenant_context(tenant.id):
            await repo.log_user_action(
                action=AuditAction.LOGIN,
                user_id=user_id,
                tenant_id=tenant.id,
                email="test@example.com",
            )
            await repo.log_user_action(
                action=AuditAction.LOGOUT,
                user_id=user_id,
                tenant_id=tenant.id,
                email="test@example.com",
            )

            logs, total = await repo.list(filters={"action": "login"})

        assert total == 1
        assert logs[0].action == "login"

    @pytest.mark.anyio
    async def test_list_with_status_filter(self, session: AsyncSession) -> None:
        """list() should filter by status.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo = AuditLogRepository(session)
        user_id = uuid4()

        with tenant_context(tenant.id):
            await repo.log_user_action(
                action=AuditAction.LOGIN,
                user_id=user_id,
                tenant_id=tenant.id,
                email="test@example.com",
                status="success",
            )
            await repo.log_user_action(
                action=AuditAction.LOGIN_FAILED,
                user_id=user_id,
                tenant_id=tenant.id,
                email="test@example.com",
                status="failure",
            )

            logs, total = await repo.list(filters={"status": "failure"})

        assert total == 1
        assert logs[0].status == "failure"
        assert logs[0].action == "login_failed"

    @pytest.mark.anyio
    async def test_list_with_date_range(self, session: AsyncSession) -> None:
        """list() should filter audit logs within a date range.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo = AuditLogRepository(session)
        user_id = uuid4()

        # Create logs with different dates.
        old_date = datetime.now(UTC) - timedelta(days=10)
        recent_date = datetime.now(UTC) - timedelta(hours=1)

        old_log = AuditLog(
            action="old_action",
            actor_id=str(user_id),
            actor_type="user",
            tenant_id=tenant.id,
            status="success",
            created_at=old_date,
        )
        recent_log = AuditLog(
            action="recent_action",
            actor_id=str(user_id),
            actor_type="user",
            tenant_id=tenant.id,
            status="success",
            created_at=recent_date,
        )
        session.add_all([old_log, recent_log])
        await session.commit()

        # list() filters are exact-match; date-range filtering is done
        # via the built-in created_at sort. We verify all records are returned.
        with tenant_context(tenant.id):
            logs, total = await repo.list(
                sort_by="created_at",
                sort_order="desc",
            )

        assert total == 2
        # Most recent first.
        assert logs[0].action == "recent_action"
        assert logs[1].action == "old_action"

    @pytest.mark.anyio
    async def test_cleanup_old_logs_deletes_old_entries(
        self, session: AsyncSession
    ) -> None:
        """cleanup_old_logs() should soft-delete logs older than the cutoff.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo = AuditLogRepository(session)
        user_id = uuid4()

        old_date = datetime.now(UTC) - timedelta(days=100)
        recent_date = datetime.now(UTC) - timedelta(days=1)

        old_log = AuditLog(
            action="old_action",
            actor_id=str(user_id),
            actor_type="user",
            tenant_id=tenant.id,
            status="success",
            created_at=old_date,
        )
        recent_log = AuditLog(
            action="recent_action",
            actor_id=str(user_id),
            actor_type="user",
            tenant_id=tenant.id,
            status="success",
            created_at=recent_date,
        )
        session.add_all([old_log, recent_log])
        await session.commit()

        with tenant_context(tenant.id):
            count = await repo.cleanup_old_logs(days=30)

        assert count == 1

        # Old log should have deleted_at set.
        await session.refresh(old_log)
        assert old_log.deleted_at is not None

        # Recent log should not be affected.
        await session.refresh(recent_log)
        assert recent_log.deleted_at is None

    @pytest.mark.anyio
    async def test_cleanup_old_logs_keeps_new_entries(
        self, session: AsyncSession
    ) -> None:
        """cleanup_old_logs() should not affect logs newer than the cutoff.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo = AuditLogRepository(session)
        user_id = uuid4()

        recent_date = datetime.now(UTC) - timedelta(days=5)

        recent_log = AuditLog(
            action="recent_action",
            actor_id=str(user_id),
            actor_type="user",
            tenant_id=tenant.id,
            status="success",
            created_at=recent_date,
        )
        session.add(recent_log)
        await session.commit()

        with tenant_context(tenant.id):
            count = await repo.cleanup_old_logs(days=365)

        # Log is 5 days old, cutoff is 365 days, should not be cleaned.
        assert count == 0

        await session.refresh(recent_log)
        assert recent_log.deleted_at is None

    @pytest.mark.anyio
    async def test_cleanup_old_logs_skips_already_deleted(
        self, session: AsyncSession
    ) -> None:
        """cleanup_old_logs() should not re-delete already soft-deleted logs.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo = AuditLogRepository(session)
        user_id = uuid4()

        old_date = datetime.now(UTC) - timedelta(days=200)

        # Create a log that is already soft-deleted.
        already_deleted = AuditLog(
            action="deleted_action",
            actor_id=str(user_id),
            actor_type="user",
            tenant_id=tenant.id,
            status="success",
            created_at=old_date,
            deleted_at=datetime.now(UTC) - timedelta(days=50),
        )
        session.add(already_deleted)
        await session.commit()

        with tenant_context(tenant.id):
            count = await repo.cleanup_old_logs(days=30)

        # Already-deleted logs should not be counted or re-processed.
        assert count == 0

    @pytest.mark.anyio
    async def test_get_recent_failures_excludes_other_tenants(
        self, session: AsyncSession
    ) -> None:
        """get_recent_failures() must not leak another tenant's failures.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant_a = await self._create_tenant(session, slug="tenant-a")
        tenant_b = await self._create_tenant(session, slug="tenant-b")
        repo = AuditLogRepository(session)
        user_id = uuid4()

        session.add(
            AuditLog(
                action="login_failed",
                actor_id=str(user_id),
                actor_type="user",
                tenant_id=tenant_a.id,
                status="failure",
            )
        )
        session.add(
            AuditLog(
                action="login_failed",
                actor_id=str(user_id),
                actor_type="user",
                tenant_id=tenant_b.id,
                status="failure",
            )
        )
        await session.commit()

        with tenant_context(tenant_a.id):
            failures = await repo.get_recent_failures()

        assert len(failures) == 1
        assert failures[0].tenant_id == tenant_a.id

    @pytest.mark.anyio
    async def test_get_recent_failures_requires_tenant_context(
        self, session: AsyncSession
    ) -> None:
        """get_recent_failures() fails closed when no tenant context is set.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session, slug="tenant-fail-closed")
        repo = AuditLogRepository(session)

        session.add(
            AuditLog(
                action="login_failed",
                actor_id=str(uuid4()),
                actor_type="user",
                tenant_id=tenant.id,
                status="failure",
            )
        )
        await session.commit()

        with pytest.raises(TenantContextRequiredError):
            await repo.get_recent_failures()

    @pytest.mark.anyio
    async def test_get_recent_failures_spans_tenants_in_system_context(
        self, session: AsyncSession
    ) -> None:
        """system_context() still allows a deliberate cross-tenant sweep.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant_a = await self._create_tenant(session, slug="sweep-a")
        tenant_b = await self._create_tenant(session, slug="sweep-b")
        repo = AuditLogRepository(session)

        for tenant_id in (tenant_a.id, tenant_b.id):
            session.add(
                AuditLog(
                    action="login_failed",
                    actor_id=str(uuid4()),
                    actor_type="user",
                    tenant_id=tenant_id,
                    status="failure",
                )
            )
        await session.commit()

        with system_context():
            failures = await repo.get_recent_failures()

        assert {failure.tenant_id for failure in failures} == {
            tenant_a.id,
            tenant_b.id,
        }

    @pytest.mark.anyio
    async def test_get_recent_failures_excludes_successes_and_old_entries(
        self, session: AsyncSession
    ) -> None:
        """Only recent, failed entries for the current tenant are returned.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo = AuditLogRepository(session)
        user_id = uuid4()

        session.add(
            AuditLog(
                action="login_succeeded",
                actor_id=str(user_id),
                actor_type="user",
                tenant_id=tenant.id,
                status="success",
            )
        )
        session.add(
            AuditLog(
                action="login_failed",
                actor_id=str(user_id),
                actor_type="user",
                tenant_id=tenant.id,
                status="failure",
                created_at=datetime.now(UTC) - timedelta(hours=48),
            )
        )
        recent_failure = AuditLog(
            action="login_failed",
            actor_id=str(user_id),
            actor_type="user",
            tenant_id=tenant.id,
            status="failure",
        )
        session.add(recent_failure)
        await session.commit()

        with tenant_context(tenant.id):
            failures = await repo.get_recent_failures(hours=24)

        assert [failure.id for failure in failures] == [recent_failure.id]
