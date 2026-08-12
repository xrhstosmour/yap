"""Unit tests for FeatureFlagRepository database operations."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import tenant_context
from app.models.feature_flag import FeatureFlag
from app.models.tenant import Tenant
from app.repositories.feature_flag_repository import FeatureFlagRepository


class TestFeatureFlagRepository:
    """Tests for FeatureFlagRepository operations."""

    @pytest.fixture
    def anyio_backend(self) -> str:
        """Provide asyncio backend for anyio-marked tests.

        Returns:
            The backend name.
        """
        return "asyncio"

    async def _create_tenant(
        self, session: AsyncSession, slug: str = "flag-test-org"
    ) -> Tenant:
        """Create a test tenant for FK constraints.

        Args:
            session: Async database session fixture.
            slug: Unique slug for the tenant.

        Returns:
            Persisted Tenant instance.
        """
        tenant = Tenant(name="Flag Test Org", slug=slug)
        session.add(tenant)
        await session.commit()
        await session.refresh(tenant)
        return tenant

    @pytest.mark.anyio
    async def test_create_flag(self, session: AsyncSession) -> None:
        """create() should persist a flag with name, state, and description.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = FeatureFlagRepository(session)

        flag = await repo.create(
            {"name": "new_checkout_flow", "state": True, "description": "Rollout"}
        )

        assert isinstance(flag, FeatureFlag)
        assert flag.id is not None
        assert flag.name == "new_checkout_flow"
        assert flag.state is True
        assert flag.description == "Rollout"

    @pytest.mark.anyio
    async def test_get_by_name_returns_flag(self, session: AsyncSession) -> None:
        """get_by_name() should return the flag matching the name.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = FeatureFlagRepository(session)
        await repo.create({"name": "some_flag", "state": False})

        found = await repo.get_by_name("some_flag")

        assert found is not None
        assert found.name == "some_flag"
        assert found.state is False

    @pytest.mark.anyio
    async def test_get_by_name_returns_none_for_unknown(
        self, session: AsyncSession
    ) -> None:
        """get_by_name() should return None when the name does not exist.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = FeatureFlagRepository(session)

        found = await repo.get_by_name("does_not_exist")

        assert found is None

    @pytest.mark.anyio
    async def test_name_exists_returns_true(self, session: AsyncSession) -> None:
        """name_exists() should return True for an existing flag name.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = FeatureFlagRepository(session)
        await repo.create({"name": "existing_flag", "state": False})

        assert await repo.name_exists("existing_flag") is True

    @pytest.mark.anyio
    async def test_name_exists_returns_false_for_missing(
        self, session: AsyncSession
    ) -> None:
        """name_exists() should return False for a non-existent flag name.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = FeatureFlagRepository(session)

        assert await repo.name_exists("no_such_flag") is False

    @pytest.mark.anyio
    async def test_list_active_returns_created_flags(
        self, session: AsyncSession
    ) -> None:
        """list_active() should return created flags.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = FeatureFlagRepository(session)
        await repo.create({"name": "flag_one", "state": True})
        await repo.create({"name": "flag_two", "state": False})

        flags = await repo.list_active()

        names = {flag.name for flag in flags}
        assert "flag_one" in names
        assert "flag_two" in names

    @pytest.mark.anyio
    async def test_update_flag(self, session: AsyncSession) -> None:
        """update() should update the specified fields.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = FeatureFlagRepository(session)
        flag = await repo.create(
            {"name": "toggle_me", "state": False, "description": "Old"}
        )

        updated = await repo.update(flag.id, {"state": True, "description": "New"})

        assert updated is not None
        assert updated.state is True
        assert updated.description == "New"

    @pytest.mark.anyio
    async def test_delete_soft_delete(self, session: AsyncSession) -> None:
        """delete(hard=False) should soft-delete a flag.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = FeatureFlagRepository(session)
        flag = await repo.create({"name": "delete_me", "state": False})

        result = await repo.delete(flag.id, hard=False)

        assert result is True

        found = await repo.get(flag.id, include_deleted=True)
        assert found is not None
        assert found.deleted_at is not None

    @pytest.mark.anyio
    async def test_get_by_name_returns_none_after_soft_delete(
        self, session: AsyncSession
    ) -> None:
        """A soft-deleted flag must not keep evaluating as active.

        `feature_enabled()` calls `get_by_name()` on the database
        fallback tier, so an unfiltered lookup here means deleting a
        flag has no effect on its runtime evaluation.
        """
        repo = FeatureFlagRepository(session)
        flag = await repo.create({"name": "soon_deleted", "state": True})
        await repo.delete(flag.id, hard=False)

        found = await repo.get_by_name("soon_deleted")

        assert found is None

    @pytest.mark.anyio
    async def test_name_exists_false_after_soft_delete(
        self, session: AsyncSession
    ) -> None:
        """A soft-deleted flag's name must be free to reuse."""
        repo = FeatureFlagRepository(session)
        flag = await repo.create({"name": "reusable_name", "state": False})
        await repo.delete(flag.id, hard=False)

        assert await repo.name_exists("reusable_name") is False

    @pytest.mark.anyio
    async def test_tenant_scoping_on_list(self, session: AsyncSession) -> None:
        """Flags created under one tenant should not leak into another tenant's list.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant_a = await self._create_tenant(session, slug="flag-tenant-a")
        tenant_b = await self._create_tenant(session, slug="flag-tenant-b")
        repo = FeatureFlagRepository(session)

        with tenant_context(tenant_a.id):
            await repo.create({"name": "scoped_flag_a", "state": True})

        with tenant_context(tenant_b.id):
            await repo.create({"name": "scoped_flag_b", "state": True})

        with tenant_context(tenant_a.id):
            flags, total = await repo.list()

        names = {flag.name for flag in flags}
        assert "scoped_flag_a" in names
        assert "scoped_flag_b" not in names
        assert total == len([flag for flag in flags if flag.name == "scoped_flag_a"])

    @pytest.mark.anyio
    async def test_tenant_scoping_on_get_by_name(self, session: AsyncSession) -> None:
        """get_by_name() is a raw query and is not tenant-scoped by BaseRepository.

        Args:
            session: Async database session fixture.

        Returns:
            None.

        Note:
            FeatureFlagRepository.get_by_name() bypasses
            BaseRepository._apply_tenant_filter() by issuing its own select(),
            so it finds a flag by name regardless of the current tenant
            context. This test documents that current behavior.
        """
        tenant_a = await self._create_tenant(session, slug="flag-tenant-c")
        repo = FeatureFlagRepository(session)

        with tenant_context(tenant_a.id):
            await repo.create({"name": "cross_tenant_flag", "state": True})

        with tenant_context(None):
            found = await repo.get_by_name("cross_tenant_flag")

        assert found is not None
        assert found.name == "cross_tenant_flag"
