"""Unit tests for FileRepository database operations."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

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
        await self._create_tenant(session)
        user = await self._create_user(session)
        repo = FileRepository(session)

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
    async def test_increment_reference_count(self, session: AsyncSession) -> None:
        """increment_reference_count() should increase the reference count by 1.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        await self._create_tenant(session)
        user = await self._create_user(session)
        repo = FileRepository(session)

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
        await self._create_tenant(session)
        user = await self._create_user(session)
        repo = FileRepository(session)

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
        await self._create_tenant(session)
        user = await self._create_user(session)
        repo = FileRepository(session)

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
