"""Unit tests for BaseRepository CRUD operations and tenant filtering."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID
from uuid import uuid4

import pytest
from sqlalchemy import UUID as SA_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field
from sqlmodel import SQLModel
from sqlmodel import select

from app.core.pagination import MAX_PAGE_SIZE
from app.core.tenant import system_context
from app.core.tenant import tenant_context
from app.models.tenant import Tenant
from app.repositories.base import BaseRepository
from app.repositories.base import TenantContextRequiredError


class TestModel(SQLModel, table=True):
    """Concrete model for testing BaseRepository."""

    __tablename__ = "test_model"
    __test__ = False  # Prevent pytest from collecting as a test class.

    id: UUID | None = Field(default_factory=uuid4, primary_key=True)
    name: str
    tenant_id: UUID | None = Field(default=None, sa_type=SA_UUID, nullable=True)
    deleted_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TestBaseRepository:
    """Tests for BaseRepository generic CRUD operations."""

    @pytest.fixture
    def anyio_backend(self) -> str:
        """Provide asyncio backend for anyio-marked tests.

        Returns:
            The backend name.
        """
        return "asyncio"

    @pytest.fixture(autouse=True)
    async def _create_test_table(self, engine) -> None:
        """Create the test_model table before each test.

        Args:
            engine: Async SQLAlchemy engine fixture.

        Returns:
            None.
        """
        async with engine.begin() as conn:
            await conn.run_sync(TestModel.__table__.create, checkfirst=True)
        yield
        async with engine.begin() as conn:
            await conn.run_sync(TestModel.__table__.drop, checkfirst=True)

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
    async def test_create_creates_and_returns_record(
        self, session: AsyncSession
    ) -> None:
        """create() should persist a record and return the model instance.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo: BaseRepository[TestModel] = BaseRepository(session, TestModel)

        with tenant_context(tenant.id):
            record = await repo.create({"name": "test-record"})

        assert isinstance(record, TestModel)
        assert record.id is not None
        assert record.name == "test-record"

        # Verify it's persisted.
        result = await session.execute(
            select(TestModel).where(TestModel.id == record.id)
        )
        persisted = result.scalar_one_or_none()
        assert persisted is not None
        assert persisted.name == "test-record"

    @pytest.mark.anyio
    async def test_get_returns_record_by_id(self, session: AsyncSession) -> None:
        """get() should return the record matching the given ID.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo: BaseRepository[TestModel] = BaseRepository(session, TestModel)

        with tenant_context(tenant.id):
            created = await repo.create({"name": "find-me"})
            found = await repo.get(created.id)

        assert found is not None
        assert found.id == created.id
        assert found.name == "find-me"

    @pytest.mark.anyio
    async def test_get_returns_none_for_missing_id(self, session: AsyncSession) -> None:
        """get() should return None when the ID does not exist.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo: BaseRepository[TestModel] = BaseRepository(session, TestModel)

        with tenant_context(tenant.id):
            found = await repo.get(uuid4())  # Non-existent ID

        assert found is None

    @pytest.mark.anyio
    async def test_list_returns_all_records(self, session: AsyncSession) -> None:
        """list() should return all non-deleted records with total count.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo: BaseRepository[TestModel] = BaseRepository(session, TestModel)

        with tenant_context(tenant.id):
            await repo.create({"name": "alpha"})
            await repo.create({"name": "beta"})
            await repo.create({"name": "gamma"})

            records, total = await repo.list()

        assert total == 3
        assert len(records) == 3
        names = {r.name for r in records}
        assert names == {"alpha", "beta", "gamma"}

    @pytest.mark.anyio
    async def test_list_with_pagination(self, session: AsyncSession) -> None:
        """list() should respect skip and limit parameters.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo: BaseRepository[TestModel] = BaseRepository(session, TestModel)

        with tenant_context(tenant.id):
            for i in range(5):
                await repo.create({"name": f"item-{i}"})

            records, total = await repo.list(skip=1, limit=2)

        assert total == 5
        assert len(records) == 2

    @pytest.mark.anyio
    async def test_list_skip_past_total_returns_correct_total(
        self, session: AsyncSession
    ) -> None:
        """list() should still report the correct total when skip exceeds it.

        The total count rides along with each row via a window function, so
        when `skip` lands past the last matching row and the page comes back
        empty, list() must fall back to a plain count query rather than
        reporting 0.
        """
        tenant = await self._create_tenant(session)
        repo: BaseRepository[TestModel] = BaseRepository(session, TestModel)

        with tenant_context(tenant.id):
            for i in range(3):
                await repo.create({"name": f"item-{i}"})

            records, total = await repo.list(skip=10, limit=2)

        assert records == []
        assert total == 3

    @pytest.mark.anyio
    async def test_list_empty_table_returns_zero_total(
        self, session: AsyncSession
    ) -> None:
        """list() on an empty table should return no records and total=0."""
        tenant = await self._create_tenant(session)
        repo: BaseRepository[TestModel] = BaseRepository(session, TestModel)

        with tenant_context(tenant.id):
            records, total = await repo.list()

        assert records == []
        assert total == 0

    @pytest.mark.anyio
    async def test_update_updates_fields(self, session: AsyncSession) -> None:
        """update() should modify the specified fields on the record.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo: BaseRepository[TestModel] = BaseRepository(session, TestModel)

        with tenant_context(tenant.id):
            created = await repo.create({"name": "original"})
            updated = await repo.update(created.id, {"name": "changed"})

        assert updated is not None
        assert updated.name == "changed"
        assert updated.updated_at is not None

        # Verify persisted.
        result = await session.execute(
            select(TestModel).where(TestModel.id == created.id)
        )
        fresh = result.scalar_one_or_none()
        assert fresh is not None
        assert fresh.name == "changed"

    @pytest.mark.anyio
    async def test_delete_hard_delete_removes_record(
        self, session: AsyncSession
    ) -> None:
        """delete(hard=True) should permanently remove the record.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo: BaseRepository[TestModel] = BaseRepository(session, TestModel)

        with tenant_context(tenant.id):
            created = await repo.create({"name": "to-delete"})
            result = await repo.delete(created.id, hard=True)

            assert result is True

            # Verify it's gone.
            found = await repo.get(created.id)

        assert found is None

    @pytest.mark.anyio
    async def test_delete_soft_delete_sets_deleted_at(
        self, session: AsyncSession
    ) -> None:
        """delete(hard=False) should set deleted_at and bury the record.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo: BaseRepository[TestModel] = BaseRepository(session, TestModel)

        with tenant_context(tenant.id):
            created = await repo.create({"name": "soft-delete-me"})
            result = await repo.delete(created.id, hard=False)

            assert result is True

            # Record should still exist but with deleted_at set.
            # Use include_deleted=True to find it.
            found = await repo.get(created.id, include_deleted=True)

        assert found is not None
        assert found.deleted_at is not None

    @pytest.mark.anyio
    async def test_tenant_filtering(self, session: AsyncSession) -> None:
        """list() should only return records matching the current tenant.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant_a = await self._create_tenant(session, slug="tenant-a")
        tenant_b = await self._create_tenant(session, slug="tenant-b")
        repo: BaseRepository[TestModel] = BaseRepository(session, TestModel)

        with tenant_context(tenant_a.id):
            await repo.create({"name": "record-a"})
        with tenant_context(tenant_b.id):
            await repo.create({"name": "record-b"})

        # Query with tenant_a context should only return record-a.
        with tenant_context(tenant_a.id):
            records, total = await repo.list()

        assert total == 1
        assert len(records) == 1
        assert records[0].name == "record-a"

        # Query with tenant_b context should only return record-b.
        with tenant_context(tenant_b.id):
            records, total = await repo.list()

        assert total == 1
        assert len(records) == 1
        assert records[0].name == "record-b"

    @pytest.mark.anyio
    async def test_get_raises_without_tenant_context(
        self, session: AsyncSession
    ) -> None:
        """A tenant-scoped model with no context set should raise, not run
        the query unfiltered.
        """
        repo: BaseRepository[TestModel] = BaseRepository(session, TestModel)

        with pytest.raises(TenantContextRequiredError):
            await repo.get(uuid4())

    @pytest.mark.anyio
    async def test_list_raises_without_tenant_context(
        self, session: AsyncSession
    ) -> None:
        """list() on a tenant-scoped model with no context should raise."""
        repo: BaseRepository[TestModel] = BaseRepository(session, TestModel)

        with pytest.raises(TenantContextRequiredError):
            await repo.list()

    @pytest.mark.anyio
    async def test_system_context_bypasses_the_raise(
        self, session: AsyncSession
    ) -> None:
        """system_context() should allow an unfiltered, cross-tenant query."""
        tenant_a = await self._create_tenant(session, slug="tenant-sys-a")
        tenant_b = await self._create_tenant(session, slug="tenant-sys-b")
        repo: BaseRepository[TestModel] = BaseRepository(session, TestModel)

        with tenant_context(tenant_a.id):
            await repo.create({"name": "sys-record-a"})
        with tenant_context(tenant_b.id):
            await repo.create({"name": "sys-record-b"})

        with system_context():
            records, total = await repo.list()

        assert total == 2
        names = {r.name for r in records}
        assert names == {"sys-record-a", "sys-record-b"}

    @pytest.mark.anyio
    async def test_repository_overriding_the_filter_needs_no_context(
        self, session: AsyncSession
    ) -> None:
        """A repository that opts out of tenant filtering must not require one.

        `Tenant` inherits `BaseModel.tenant_id` for column-shape
        consistency (a tenant is not itself owned by another tenant), so
        `TenantRepository` overrides `_apply_tenant_filter()` as a no-op.
        `BaseRepository`'s own raise must not apply once a subclass has
        opted out this way.
        """
        from app.repositories.tenant_repository import TenantRepository

        tenant = await self._create_tenant(session, slug="tenant-no-scope")
        repo = TenantRepository(session)

        found = await repo.get(tenant.id)

        assert found is not None
        assert found.id == tenant.id

    @pytest.mark.anyio
    async def test_create_falls_back_to_system_tenant_id(
        self, session: AsyncSession
    ) -> None:
        """create() with no context and no explicit tenant_id must not
        leave the column NULL: it should fall back to SYSTEM_TENANT_ID so
        the row stays findable through the same tenant filter that would
        otherwise hide it forever.
        """
        from app.core import SYSTEM_TENANT_ID

        repo: BaseRepository[TestModel] = BaseRepository(session, TestModel)

        record = await repo.create({"name": "no-context-record"})

        assert record.tenant_id == SYSTEM_TENANT_ID

    @pytest.mark.anyio
    async def test_max_page_size_clamp(self, session: AsyncSession) -> None:
        """list() should clamp limit to MAX_PAGE_SIZE when exceeded.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo: BaseRepository[TestModel] = BaseRepository(session, TestModel)

        with tenant_context(tenant.id):
            for i in range(5):
                await repo.create({"name": f"item-{i}"})

            # Request more than MAX_PAGE_SIZE (100), verify limit is clamped.
            records, total = await repo.list(limit=200)

        assert total == 5
        assert len(records) <= MAX_PAGE_SIZE
        assert len(records) == 5  # All records fit within clamp.
