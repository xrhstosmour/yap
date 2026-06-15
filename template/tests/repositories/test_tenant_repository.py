"""Unit tests for TenantRepository database operations."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant
from app.repositories.tenant_repository import TenantRepository


class TestTenantRepository:
    """Tests for TenantRepository operations."""

    @pytest.fixture
    def anyio_backend(self) -> str:
        """Provide asyncio backend for anyio-marked tests.

        Returns:
            The backend name.
        """
        return "asyncio"

    @pytest.mark.anyio
    async def test_create_tenant(self, session: AsyncSession) -> None:
        """create_tenant() should persist a tenant with name, slug, and settings.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = TenantRepository(session)
        tenant = await repo.create_tenant(
            name="Acme Corp",
            slug="acme-corp",
            settings={"theme": "dark", "max_users": 50},
        )

        assert isinstance(tenant, Tenant)
        assert tenant.id is not None
        assert tenant.name == "Acme Corp"
        assert tenant.slug == "acme-corp"
        assert tenant.settings == {"theme": "dark", "max_users": 50}
        assert tenant.is_active is True

    @pytest.mark.anyio
    async def test_get_by_slug_returns_tenant(self, session: AsyncSession) -> None:
        """get_by_slug() should return the tenant matching the slug.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = TenantRepository(session)
        await repo.create_tenant(name="Acme Corp", slug="acme-corp")

        found = await repo.get_by_slug("acme-corp")

        assert found is not None
        assert found.name == "Acme Corp"

    @pytest.mark.anyio
    async def test_get_by_slug_returns_none_for_unknown(
        self, session: AsyncSession
    ) -> None:
        """get_by_slug() should return None when the slug does not exist.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = TenantRepository(session)

        found = await repo.get_by_slug("nonexistent-slug")

        assert found is None

    @pytest.mark.anyio
    async def test_slug_exists_returns_true(self, session: AsyncSession) -> None:
        """slug_exists() should return True for an existing slug.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = TenantRepository(session)
        await repo.create_tenant(name="Acme Corp", slug="acme-corp")

        assert await repo.slug_exists("acme-corp") is True

    @pytest.mark.anyio
    async def test_slug_exists_returns_false_for_missing(
        self, session: AsyncSession
    ) -> None:
        """slug_exists() should return False for a non-existent slug.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = TenantRepository(session)

        assert await repo.slug_exists("no-such-slug") is False

    @pytest.mark.anyio
    async def test_slug_exists_excludes_id(self, session: AsyncSession) -> None:
        """slug_exists() with exclude_id should ignore the specified tenant.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = TenantRepository(session)
        tenant = await repo.create_tenant(name="Acme Corp", slug="acme-corp")

        # slug_exists for the same slug should return False when we exclude
        # the tenant that owns it.
        assert await repo.slug_exists("acme-corp", exclude_id=tenant.id) is False

    @pytest.mark.anyio
    async def test_update_tenant(self, session: AsyncSession) -> None:
        """update_tenant() should update the specified fields.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = TenantRepository(session)
        tenant = await repo.create_tenant(name="Old Name", slug="old-slug")

        updated = await repo.update_tenant(
            tenant.id,
            name="New Name",
            is_active=False,
            settings={"new": True},
        )

        assert updated is not None
        assert updated.name == "New Name"
        assert updated.is_active is False
        assert updated.settings == {"new": True}
        # slug should remain unchanged since we didn't pass it.
        assert updated.slug == "old-slug"

    @pytest.mark.anyio
    async def test_delete_soft_delete(self, session: AsyncSession) -> None:
        """delete(hard=False) should soft-delete a tenant.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = TenantRepository(session)
        tenant = await repo.create_tenant(
            name="To Delete", slug="to-delete"
        )

        result = await repo.delete(tenant.id, hard=False)

        assert result is True

        # Should not appear in list().
        tenants, total = await repo.list()
        assert total == 0

        # Should still exist with deleted_at set.
        found = await repo.get(tenant.id, include_deleted=True)
        assert found is not None
        assert found.deleted_at is not None
