"""Unit tests for APIKeyRepository database operations."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import tenant_context
from app.models.api_key import APIKey
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.api_key_repository import APIKeyRepository


class TestAPIKeyRepository:
    """Tests for APIKeyRepository operations."""

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

    @pytest.mark.anyio
    async def test_get_by_key_id_returns_key(self, session: AsyncSession) -> None:
        """get_by_key_id() should return the API key for a valid key_id.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        user = await self._create_user(session)

        key_data = {
            "key_id": "pk_test_abc123",
            "key_hash": "hashed_secret",
            "key_prefix": "pk_test_",
            "name": "Test Key",
            "scopes": ["read"],
            "tenant_id": tenant.id,
            "user_id": user.id,
        }
        key = APIKey(**key_data)
        session.add(key)
        await session.commit()

        repo = APIKeyRepository(session)
        found = await repo.get_by_key_id("pk_test_abc123")

        assert found is not None
        assert found.key_id == "pk_test_abc123"
        assert found.name == "Test Key"

    @pytest.mark.anyio
    async def test_get_by_key_id_returns_none_for_missing(
        self, session: AsyncSession
    ) -> None:
        """get_by_key_id() should return None for a non-existent key_id.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = APIKeyRepository(session)
        found = await repo.get_by_key_id("nonexistent_key")

        assert found is None

    @pytest.mark.anyio
    async def test_list_filters_by_user_id(self, session: AsyncSession) -> None:
        """list() with filters should return only keys for a given user_id.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        user_a = await self._create_user(session, "a@example.com")
        user_b = await self._create_user(session, "b@example.com")

        key_a = APIKey(
            key_id="pk_test_a",
            key_hash="hash_a",
            key_prefix="pk_",
            name="Key A",
            tenant_id=tenant.id,
            user_id=user_a.id,
        )
        key_b = APIKey(
            key_id="pk_test_b",
            key_hash="hash_b",
            key_prefix="pk_",
            name="Key B",
            tenant_id=tenant.id,
            user_id=user_b.id,
        )
        session.add_all([key_a, key_b])
        await session.commit()

        repo = APIKeyRepository(session)
        with tenant_context(tenant.id):
            keys, total = await repo.list(filters={"user_id": user_a.id})

        assert total == 1
        assert len(keys) == 1
        assert keys[0].name == "Key A"

    @pytest.mark.anyio
    async def test_list_by_user_excludes_inactive_by_default(
        self, session: AsyncSession
    ) -> None:
        """list_by_user() should exclude inactive keys when include_inactive is False.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        user = await self._create_user(session)

        active_key = APIKey(
            key_id="pk_active",
            key_hash="hash_active",
            key_prefix="pk_",
            name="Active Key",
            tenant_id=tenant.id,
            user_id=user.id,
            is_active=True,
        )
        inactive_key = APIKey(
            key_id="pk_inactive",
            key_hash="hash_inactive",
            key_prefix="pk_",
            name="Inactive Key",
            tenant_id=tenant.id,
            user_id=user.id,
            is_active=False,
        )
        session.add_all([active_key, inactive_key])
        await session.commit()

        repo = APIKeyRepository(session)

        with tenant_context(tenant.id):
            # Default: exclude inactive.
            keys, total = await repo.list_by_user(user.id)
            assert total == 1
            assert keys[0].name == "Active Key"

            # Include inactive.
            keys, total = await repo.list_by_user(user.id, include_inactive=True)

        assert total == 2

    @pytest.mark.anyio
    async def test_soft_delete_behavior(self, session: AsyncSession) -> None:
        """Soft-deleted API keys should be excluded from queries by default.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        user = await self._create_user(session)

        key = APIKey(
            key_id="pk_to_delete",
            key_hash="hash",
            key_prefix="pk_",
            name="Delete Me",
            tenant_id=tenant.id,
            user_id=user.id,
        )
        session.add(key)
        await session.commit()
        await session.refresh(key)

        repo = APIKeyRepository(session)

        with tenant_context(tenant.id):
            # Soft delete.
            result = await repo.delete(key.id, hard=False)
            assert result is True

            # Should not appear in list().
            keys, total = await repo.list()
            assert total == 0

            # Should not appear in list_by_user().
            keys, total = await repo.list_by_user(user.id)

        assert total == 0

    @pytest.mark.anyio
    async def test_compound_indexes_exist(self, session: AsyncSession) -> None:
        """Verify that compound indexes are defined on the api_keys table.

        This is a smoke test that confirms the table metadata includes
        the expected compound index definitions. Indexes are verified
        through the model's __table_args__ metadata.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        assert "__table_args__" in APIKey.__dict__
        indexes = APIKey.__table_args__
        index_names = [idx.name for idx in indexes if hasattr(idx, "name")]
        assert "ix_api_keys_user_id_is_active" in index_names
        assert "ix_api_keys_expires_at_is_active" in index_names
