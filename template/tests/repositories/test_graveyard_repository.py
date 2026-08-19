"""Unit tests for GraveyardRepository."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import tenant_context
from app.models.graveyard import Graveyard
from app.models.tenant import Tenant
from app.repositories.graveyard_repository import GraveyardRepository


class TestGraveyardRepository:
    """Tests for GraveyardRepository bury() and purge() methods."""

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
        """Create a test tenant to satisfy graveyard FK constraint.

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
    async def test_bury_creates_graveyard_entry(self, session: AsyncSession) -> None:
        """Calling bury() should persist a Graveyard row and return it.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo = GraveyardRepository(session)
        record_id = uuid4()

        entry = await repo.bury(
            model_name="users",
            record_id=record_id,
            data={"email": "alice@example.com"},
            tenant_id=tenant.id,
        )

        assert isinstance(entry, Graveyard)
        assert entry.model_name == "users"
        assert entry.record_id == record_id
        assert entry.tenant_id == tenant.id

        # Verify the row is actually persisted in the database.
        result = await session.execute(
            select(Graveyard).where(Graveyard.id == entry.id)
        )
        persisted = result.scalar_one_or_none()
        assert persisted is not None
        assert persisted.model_name == "users"

    @pytest.mark.anyio
    async def test_bury_sets_correct_fields(self, session: AsyncSession) -> None:
        """bury() should populate every field with the value provided.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo = GraveyardRepository(session)
        record_id = uuid4()

        entry = await repo.bury(
            model_name="api_keys",
            record_id=record_id,
            data={"key": "abc123", "scope": "read"},
            deleted_by="admin-user-9",
            reason="revoked by admin",
            tenant_id=tenant.id,
        )

        assert entry.model_name == "api_keys"
        assert entry.record_id == record_id
        assert entry.data == {"key": "abc123", "scope": "read"}
        assert entry.deleted_by == "admin-user-9"
        assert entry.reason == "revoked by admin"
        assert entry.tenant_id == tenant.id
        assert entry.record_deleted_at is not None

        # SQLite stores datetimes as naive UTC.  Make the stored value
        # aware for comparison so the subtraction does not fail with a
        # naive-vs-aware TypeError.
        stored_aware = entry.record_deleted_at.replace(tzinfo=UTC)
        age = datetime.now(UTC) - stored_aware
        assert age.total_seconds() < 60

    @pytest.mark.anyio
    async def test_bury_defaults_deleted_by_to_system(
        self, session: AsyncSession
    ) -> None:
        """bury() should default deleted_by to 'system' when not provided.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo = GraveyardRepository(session)

        entry = await repo.bury(
            model_name="users",
            record_id=uuid4(),
            data={},
            tenant_id=tenant.id,
        )

        assert entry.deleted_by == "system"

    @pytest.mark.anyio
    async def test_bury_defaults_reason_to_none(self, session: AsyncSession) -> None:
        """bury() should default reason to None when not provided.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo = GraveyardRepository(session)

        entry = await repo.bury(
            model_name="users",
            record_id=uuid4(),
            data={},
            tenant_id=tenant.id,
        )

        assert entry.reason is None

    @pytest.mark.anyio
    async def test_recover_returns_data_within_same_tenant(
        self, session: AsyncSession
    ) -> None:
        """recover() returns the snapshot when called within the owning tenant."""
        tenant = await self._create_tenant(session)
        repo = GraveyardRepository(session)
        record_id = uuid4()
        await repo.bury(
            model_name="users",
            record_id=record_id,
            data={"email": "alice@example.com"},
            tenant_id=tenant.id,
        )

        with tenant_context(tenant.id):
            data = await repo.recover(record_id)

        assert data == {"email": "alice@example.com"}

    @pytest.mark.anyio
    async def test_recover_returns_none_for_a_different_tenant(
        self, session: AsyncSession
    ) -> None:
        """recover() must not return another tenant's graveyard snapshot.

        A graveyard entry holds every column of the original row, PII and
        secret hashes included. Without tenant scoping, one tenant could
        recover another tenant's deleted record by guessing its UUID.
        """
        owning_tenant = await self._create_tenant(session, slug="owning-org")
        other_tenant = await self._create_tenant(session, slug="other-org")
        repo = GraveyardRepository(session)
        record_id = uuid4()
        await repo.bury(
            model_name="users",
            record_id=record_id,
            data={"email": "alice@example.com"},
            tenant_id=owning_tenant.id,
        )

        with tenant_context(other_tenant.id):
            data = await repo.recover(record_id)

        assert data is None

    @pytest.mark.anyio
    async def test_recover_returns_none_for_unknown_record(
        self, session: AsyncSession
    ) -> None:
        """recover() returns None when no graveyard entry matches."""
        tenant = await self._create_tenant(session)
        repo = GraveyardRepository(session)

        with tenant_context(tenant.id):
            data = await repo.recover(uuid4())

        assert data is None

    @pytest.mark.anyio
    async def test_purge_removes_old_records(self, session: AsyncSession) -> None:
        """purge() should delete graveyard entries older than the retention period.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo = GraveyardRepository(session)
        old_date = datetime.now(UTC) - timedelta(days=100)

        old_entry = Graveyard(
            model_name="users",
            record_id=uuid4(),
            data={"email": "old@example.com"},
            record_deleted_at=old_date,
            tenant_id=tenant.id,
        )
        session.add(old_entry)
        await session.commit()

        count = await repo.purge(retention_days=90)
        assert count == 1

        # The old entry should no longer exist.
        result = await session.execute(
            select(Graveyard).where(Graveyard.id == old_entry.id)
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.anyio
    async def test_purge_keeps_recent_records(self, session: AsyncSession) -> None:
        """purge() should not remove entries newer than the retention period.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo = GraveyardRepository(session)
        old_date = datetime.now(UTC) - timedelta(days=100)
        recent_date = datetime.now(UTC) - timedelta(days=10)

        old_entry = Graveyard(
            model_name="users",
            record_id=uuid4(),
            data={"email": "old@example.com"},
            record_deleted_at=old_date,
            tenant_id=tenant.id,
        )
        recent_entry = Graveyard(
            model_name="api_keys",
            record_id=uuid4(),
            data={"key": "recent-key"},
            record_deleted_at=recent_date,
            tenant_id=tenant.id,
        )
        session.add_all([old_entry, recent_entry])
        await session.commit()

        count = await repo.purge(retention_days=90)
        assert count == 1

        # The recent entry should still be present.
        result = await session.execute(
            select(Graveyard).where(Graveyard.id == recent_entry.id)
        )
        assert result.scalar_one_or_none() is not None

        # The old entry should be gone.
        result = await session.execute(
            select(Graveyard).where(Graveyard.id == old_entry.id)
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.anyio
    async def test_purge_uses_default_retention_when_not_specified(
        self, session: AsyncSession
    ) -> None:
        """purge() should use DEFAULT_RETENTION_DAYS when retention_days is omitted.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        from app.models.graveyard import DEFAULT_RETENTION_DAYS

        tenant = await self._create_tenant(session)
        repo = GraveyardRepository(session)
        very_old_date = datetime.now(UTC) - timedelta(days=DEFAULT_RETENTION_DAYS + 20)

        old_entry = Graveyard(
            model_name="users",
            record_id=uuid4(),
            data={},
            record_deleted_at=very_old_date,
            tenant_id=tenant.id,
        )
        session.add(old_entry)
        await session.commit()

        count = await repo.purge()
        assert count == 1

    @pytest.mark.anyio
    async def test_multiple_buries_and_purge_count(self, session: AsyncSession) -> None:
        """Bury several records, age some, then purge and verify exact count.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo = GraveyardRepository(session)

        # Bury 5 records.
        entries = []
        for i in range(5):
            entry = await repo.bury(
                model_name="users",
                record_id=uuid4(),
                data={"index": i},
                tenant_id=tenant.id,
            )
            entries.append(entry)

        # Age the first 2 entries to be older than 90 days.
        # Use a naive datetime, SQLite discards timezone info, and the
        # ORM bulk-delete post-synchronize step would fail comparing
        # naive stored values against an aware purge cutoff.
        old_date = (datetime.now(UTC) - timedelta(days=120)).replace(tzinfo=None)
        await session.execute(
            update(Graveyard)
            .where(Graveyard.id.in_([entries[0].id, entries[1].id]))
            .values(record_deleted_at=old_date)
        )
        await session.commit()

        # After bury+refresh the in-memory objects hold naive
        # datetimes (SQLite discards tzinfo).  The bulk-delete
        # post-synchronize evaluator would choke comparing those
        # against the aware cutoff inside purge().  Expunge them
        # so the evaluator has nothing to walk.
        for entry in entries:
            session.expunge(entry)

        count = await repo.purge(retention_days=90)
        assert count == 2

        # Entries 2-4 should still exist.
        remaining_ids = [entries[2].id, entries[3].id, entries[4].id]
        result = await session.execute(
            select(Graveyard).where(Graveyard.id.in_(remaining_ids))
        )
        survivors = result.scalars().all()
        assert len(survivors) == 3

    @pytest.mark.anyio
    async def test_purge_returns_zero_when_nothing_to_purge(
        self, session: AsyncSession
    ) -> None:
        """purge() should return 0 when all entries are within retention.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo = GraveyardRepository(session)

        recent_entry = Graveyard(
            model_name="users",
            record_id=uuid4(),
            data={},
            record_deleted_at=datetime.now(UTC) - timedelta(days=5),
            tenant_id=tenant.id,
        )
        session.add(recent_entry)
        await session.commit()

        count = await repo.purge(retention_days=30)
        assert count == 0

        # Entry should still be there.
        result = await session.execute(
            select(Graveyard).where(Graveyard.id == recent_entry.id)
        )
        assert result.scalar_one_or_none() is not None
