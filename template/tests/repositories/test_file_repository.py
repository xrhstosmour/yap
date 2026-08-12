"""Unit tests for FileRepository database operations."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from sqlalchemy import delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import tenant_context
from app.models.file import File
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.file_repository import FileRepository


class TestFileRepository:
    """Tests for FileRepository operations."""

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

    async def _create_user(
        self, session: AsyncSession, email: str = "test@example.com"
    ) -> User:
        """Create a test user for FK constraints.

        Args:
            session: Async database session fixture.
            email: Email for the user.

        Returns:
            Persisted User instance.
        """
        user = User(email=email, hashed_password="hash")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user

    def _file_data(self, uploaded_by, **overrides):
        """Build file creation data with sensible defaults.

        Args:
            uploaded_by: UUID of the uploading user.
            **overrides: Fields to override.

        Returns:
            Dict of file field values.
        """
        return {
            "filename": "test.txt",
            "mimetype": "text/plain",
            "size": 1024,
            "content_hash": "abc123def456",
            "bucket": "my-bucket",
            "object_key": "uploads/test.txt",
            "uploaded_by": uploaded_by,
            **overrides,
        }

    @pytest.mark.anyio
    async def test_create_file_record(self, session: AsyncSession) -> None:
        """create() should persist a file record and return it.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        await self._create_tenant(session)
        user = await self._create_user(session)
        repo = FileRepository(session)

        file_record = await repo.create(self._file_data(uploaded_by=user.id))

        assert isinstance(file_record, File)
        assert file_record.id is not None
        assert file_record.filename == "test.txt"
        assert file_record.mimetype == "text/plain"
        assert file_record.size == 1024
        assert file_record.content_hash == "abc123def456"
        assert file_record.reference_count == 1

    @pytest.mark.anyio
    async def test_get_owned_returns_file_for_correct_user(
        self, session: AsyncSession
    ) -> None:
        """get_owned() should return the file when the user owns it.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        await self._create_tenant(session)
        user = await self._create_user(session)
        repo = FileRepository(session)

        file_record = await repo.create(self._file_data(uploaded_by=user.id))

        found = await repo.get_owned(file_record.id, user.id)

        assert found is not None
        assert found.id == file_record.id
        assert found.filename == "test.txt"

    @pytest.mark.anyio
    async def test_get_owned_returns_none_for_wrong_user(
        self, session: AsyncSession
    ) -> None:
        """get_owned() should return None when the user does not own the file.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        await self._create_tenant(session)
        owner = await self._create_user(session, "owner@example.com")
        other = await self._create_user(session, "other@example.com")
        repo = FileRepository(session)

        file_record = await repo.create(self._file_data(uploaded_by=owner.id))

        found = await repo.get_owned(file_record.id, other.id)

        assert found is None

    @pytest.mark.anyio
    async def test_get_by_content_hash(self, session: AsyncSession) -> None:
        """get_by_content_hash() should find a file by its content hash.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        user = await self._create_user(session)
        repo = FileRepository(session)

        with tenant_context(tenant.id):
            await repo.create(
                self._file_data(uploaded_by=user.id, content_hash="unique-hash-001")
            )

            found = await repo.get_by_content_hash("unique-hash-001")

        assert found is not None
        assert found.content_hash == "unique-hash-001"

    @pytest.mark.anyio
    async def test_get_by_content_hash_returns_none_for_unknown(
        self, session: AsyncSession
    ) -> None:
        """get_by_content_hash() should return None for an unknown hash.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = FileRepository(session)
        found = await repo.get_by_content_hash("nonexistent-hash")

        assert found is None

    @pytest.mark.anyio
    async def test_create_or_increment_scopes_dedup_per_tenant(
        self, session: AsyncSession
    ) -> None:
        """Two tenants uploading identical content must not share a row.

        Deduplication is per-tenant (see the ``File`` model docstring):
        a second tenant uploading content identical to a first tenant's
        must get its own row, own ``uploaded_by``, and remain reachable
        via ``get_owned()`` by its own uploader, not silently locked out
        by the first tenant's row.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant_a = await self._create_tenant(session, slug="tenant-a")
        tenant_b = await self._create_tenant(session, slug="tenant-b")
        user_a = await self._create_user(session, email="a@example.com")
        user_b = await self._create_user(session, email="b@example.com")
        repo = FileRepository(session)

        with tenant_context(tenant_a.id):
            record_a, created_a = await repo.create_or_increment(
                self._file_data(
                    uploaded_by=user_a.id,
                    content_hash="shared-across-tenants",
                    object_key="uploads/a.txt",
                )
            )

        with tenant_context(tenant_b.id):
            record_b, created_b = await repo.create_or_increment(
                self._file_data(
                    uploaded_by=user_b.id,
                    content_hash="shared-across-tenants",
                    object_key="uploads/b.txt",
                )
            )

        assert created_a is True
        assert created_b is True
        assert record_a.id != record_b.id
        assert record_a.uploaded_by == user_a.id
        assert record_b.uploaded_by == user_b.id

        with tenant_context(tenant_b.id):
            owned_by_b = await repo.get_owned(record_b.id, user_b.id)
        assert owned_by_b is not None

    @pytest.mark.anyio
    async def test_create_or_increment_resolves_concurrent_upload_race(
        self, engine: AsyncEngine
    ) -> None:
        """Two concurrent uploads of identical content should race safely.

        Uses two independent sessions (separate connections, real commits)
        so the database itself resolves the race via the unique constraint
        on ``content_hash``, rather than the two coroutines serializing on
        a single shared connection. Exactly one ``File`` row must exist
        afterward, with ``reference_count == 2``.

        Args:
            engine: Async engine fixture, used to open independent sessions.

        Returns:
            None.
        """
        content_hash = "concurrent-upload-hash"

        async with AsyncSession(engine, expire_on_commit=False) as setup_session:
            user = await self._create_user(setup_session, "racer@example.com")

        async def _attempt() -> tuple[UUID, bool]:
            async with AsyncSession(engine, expire_on_commit=False) as own_session:
                repo = FileRepository(own_session)
                record, created = await repo.create_or_increment(
                    self._file_data(uploaded_by=user.id, content_hash=content_hash)
                )
                await own_session.commit()
                return record.id, created

        try:
            results = await asyncio.gather(_attempt(), _attempt())

            # Exactly one attempt inserted, the other incremented.
            assert sorted(created for _, created in results) == [False, True]
            # Both attempts must have landed on the same row.
            assert len({record_id for record_id, _ in results}) == 1

            async with AsyncSession(engine, expire_on_commit=False) as verify_session:
                found = await verify_session.execute(
                    select(File).where(File.content_hash == content_hash)
                )
                rows = found.scalars().all()
                assert len(rows) == 1
                assert rows[0].reference_count == 2
        finally:
            async with AsyncSession(engine, expire_on_commit=False) as cleanup_session:
                await cleanup_session.execute(
                    delete(File).where(File.content_hash == content_hash)
                )
                await cleanup_session.execute(delete(User).where(User.id == user.id))
                await cleanup_session.commit()

    @pytest.mark.anyio
    async def test_increment_reference_count(self, session: AsyncSession) -> None:
        """increment_reference_count() should increase the reference count by 1.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        user = await self._create_user(session)
        repo = FileRepository(session)

        with tenant_context(tenant.id):
            file_record = await repo.create(
                self._file_data(uploaded_by=user.id, content_hash="hash-to-inc")
            )
            assert file_record.reference_count == 1

            await repo.increment_reference_count("hash-to-inc")

            # Refresh to get updated count.
            updated = await repo.get(file_record.id)

        assert updated is not None
        assert updated.reference_count == 2

    @pytest.mark.anyio
    async def test_decrement_reference_count(self, session: AsyncSession) -> None:
        """decrement_reference_count() should decrease the reference count by 1.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        user = await self._create_user(session)
        repo = FileRepository(session)

        with tenant_context(tenant.id):
            file_record = await repo.create(
                self._file_data(uploaded_by=user.id, content_hash="hash-to-dec")
            )
            # Increment first so we have room to decrement.
            await repo.increment_reference_count("hash-to-dec")

            new_count = await repo.decrement_reference_count(file_record.id)

            assert new_count == 1
            updated = await repo.get(file_record.id)

        assert updated is not None
        assert updated.reference_count == 1

    @pytest.mark.anyio
    async def test_decrement_reference_count_does_not_go_below_zero(
        self, session: AsyncSession
    ) -> None:
        """decrement_reference_count() should not go below 0.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        user = await self._create_user(session)
        repo = FileRepository(session)

        with tenant_context(tenant.id):
            file_record = await repo.create(
                self._file_data(uploaded_by=user.id, content_hash="hash-min")
            )
            assert file_record.reference_count == 1

            new_count = await repo.decrement_reference_count(file_record.id)

            assert new_count == 0
            updated = await repo.get(file_record.id)

            assert updated is not None
            assert updated.reference_count == 0

            # Decrement again should stay at 0.
            new_count = await repo.decrement_reference_count(file_record.id)

        assert new_count == 0

    @pytest.mark.anyio
    async def test_delete_soft_delete(self, session: AsyncSession) -> None:
        """delete(hard=False) should soft-delete the file record.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        user = await self._create_user(session)
        repo = FileRepository(session)

        with tenant_context(tenant.id):
            file_record = await repo.create(
                self._file_data(uploaded_by=user.id, tenant_id=tenant.id)
            )

            result = await repo.delete(file_record.id, hard=False)

            assert result is True

            # File should not appear in list() but should still exist.
            records, total = await repo.list()
            assert total == 0

            found = await repo.get(file_record.id, include_deleted=True)

        assert found is not None
        assert found.deleted_at is not None
