"""Tests for FeatureFlagService."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import UUID
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import tenant_context
from app.models.feature_flag import FeatureFlag
from app.schemas.feature_flag import FeatureFlagCreate
from app.schemas.feature_flag import FeatureFlagUpdate
from app.services.feature_flag_service import FeatureFlagService
from app.services.feature_flag_service import FeatureFlagServiceError


@pytest.fixture
def mock_session() -> MagicMock:
    return MagicMock()


@pytest.fixture
def service(mock_session: MagicMock) -> FeatureFlagService:
    svc = FeatureFlagService(mock_session)
    svc.repository = AsyncMock()
    return svc


def _make_flag(
    name: str = "test_flag",
    state: bool = True,
    description: str | None = "Test flag",
    flag_id: UUID | None = None,
) -> FeatureFlag:
    """Factory helper for creating FeatureFlag instances in tests."""
    return FeatureFlag(
        id=flag_id or UUID("00000000-0000-0000-0000-000000000001"),
        name=name,
        state=state,
        description=description,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        updated_at=datetime(2025, 1, 1, tzinfo=UTC),
    )


class TestCreateFlag:
    """Tests for create_flag()."""

    @pytest.mark.asyncio
    async def test_create_flag_success(self, service: FeatureFlagService) -> None:
        """Should create a flag when name does not exist."""
        service.repository.name_exists = AsyncMock(return_value=False)
        flag = _make_flag()
        service.repository.create = AsyncMock(return_value=flag)

        with patch(
            "app.services.feature_flag_service.ff.refresh_cache", new_callable=AsyncMock
        ) as mock_refresh:
            result = await service.create_flag(
                FeatureFlagCreate(name="test_flag", state=True, description="Test flag")
            )

        assert result is flag
        assert result.name == "test_flag"
        assert result.state is True
        service.repository.name_exists.assert_awaited_once_with("test_flag")
        service.repository.create.assert_awaited_once_with(
            {"name": "test_flag", "state": True, "description": "Test flag"}
        )
        mock_refresh.assert_awaited_once_with("test_flag")

    @pytest.mark.asyncio
    async def test_create_flag_duplicate_name_raises(
        self, service: FeatureFlagService
    ) -> None:
        """Should raise FeatureFlagServiceError when name already exists."""
        service.repository.name_exists = AsyncMock(return_value=True)

        with pytest.raises(
            FeatureFlagServiceError, match="Feature flag 'test_flag' already exists"
        ):
            await service.create_flag(FeatureFlagCreate(name="test_flag", state=True))

        service.repository.name_exists.assert_awaited_once_with("test_flag")
        service.repository.create.assert_not_awaited()


class TestGetByName:
    """Tests for get_by_name()."""

    @pytest.mark.asyncio
    async def test_get_by_name_found(self, service: FeatureFlagService) -> None:
        """Should return the flag when found by name."""
        flag = _make_flag(name="my_feature")
        service.repository.get_by_name = AsyncMock(return_value=flag)

        result = await service.get_by_name("my_feature")

        assert result is flag
        assert result.name == "my_feature"
        service.repository.get_by_name.assert_awaited_once_with("my_feature")

    @pytest.mark.asyncio
    async def test_get_by_name_not_found(self, service: FeatureFlagService) -> None:
        """Should return None when flag is not found."""
        service.repository.get_by_name = AsyncMock(return_value=None)

        result = await service.get_by_name("nonexistent")

        assert result is None
        service.repository.get_by_name.assert_awaited_once_with("nonexistent")


class TestListFlags:
    """Tests for list_flags()."""

    @pytest.mark.asyncio
    async def test_list_flags_returns_tuple(self, service: FeatureFlagService) -> None:
        """Should return a tuple of (flags, total_count)."""
        flag1 = _make_flag(
            name="flag_a", flag_id=UUID("00000000-0000-0000-0000-000000000001")
        )
        flag2 = _make_flag(
            name="flag_b", flag_id=UUID("00000000-0000-0000-0000-000000000002")
        )
        service.repository.list = AsyncMock(return_value=([flag1, flag2], 2))

        flags, total = await service.list_flags()

        assert len(flags) == 2
        assert total == 2
        assert flags[0].name == "flag_a"
        assert flags[1].name == "flag_b"
        service.repository.list.assert_awaited_once_with(skip=0, limit=20)


class TestUpdateFlag:
    """Tests for update_flag()."""

    @pytest.mark.asyncio
    async def test_update_flag_description(self, service: FeatureFlagService) -> None:
        """Should update only the description and not touch the cache."""
        existing = _make_flag(name="test_flag", description="Old desc")
        updated = _make_flag(name="test_flag", description="New desc")
        service.repository.get_by_name = AsyncMock(return_value=existing)
        service.repository.update = AsyncMock(return_value=updated)

        with patch(
            "app.services.feature_flag_service.ff.refresh_cache", new_callable=AsyncMock
        ) as mock_refresh:
            result = await service.update_flag(
                "test_flag",
                FeatureFlagUpdate(description="New desc"),
            )

        assert result is not None
        assert result.description == "New desc"
        service.repository.get_by_name.assert_awaited_once_with("test_flag")
        service.repository.update.assert_awaited_once_with(
            existing.id, {"description": "New desc"}
        )
        mock_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_flag_not_found(self, service: FeatureFlagService) -> None:
        """Should return None when flag does not exist."""
        service.repository.get_by_name = AsyncMock(return_value=None)

        result = await service.update_flag(
            "missing", FeatureFlagUpdate(description="ignored")
        )

        assert result is None
        service.repository.update.assert_not_awaited()


class TestToggleFlag:
    """Tests for toggle_flag()."""

    @pytest.mark.asyncio
    async def test_toggle_flag_true_to_false(self, service: FeatureFlagService) -> None:
        """Should toggle state from True to False and drop the cache entry."""
        existing = _make_flag(name="test_flag", state=True)
        toggled = _make_flag(name="test_flag", state=False)
        service.repository.get_by_name = AsyncMock(return_value=existing)
        service.repository.update = AsyncMock(return_value=toggled)

        with patch(
            "app.services.feature_flag_service.ff.refresh_cache", new_callable=AsyncMock
        ) as mock_refresh:
            result = await service.toggle_flag("test_flag", False)

        assert result is not None
        assert result.state is False
        service.repository.get_by_name.assert_awaited_once_with("test_flag")
        mock_refresh.assert_awaited_once_with("test_flag")


class TestDeleteFlag:
    """Tests for delete_flag()."""

    @pytest.mark.asyncio
    async def test_delete_flag_success(self, service: FeatureFlagService) -> None:
        """Should delete the flag, return True, and remove from Redis."""
        flag = _make_flag(name="test_flag")
        service.repository.get_by_name = AsyncMock(return_value=flag)
        service.repository.delete = AsyncMock(return_value=True)

        with patch(
            "app.services.feature_flag_service.ff.remove_from_redis",
            new_callable=AsyncMock,
        ) as mock_remove:
            result = await service.delete_flag("test_flag")

        assert result is True
        service.repository.get_by_name.assert_awaited_once_with("test_flag")
        service.repository.delete.assert_awaited_once_with(flag.id)
        mock_remove.assert_awaited_once_with("test_flag")

    @pytest.mark.asyncio
    async def test_delete_flag_not_found(self, service: FeatureFlagService) -> None:
        """Should return False when flag does not exist."""
        service.repository.get_by_name = AsyncMock(return_value=None)

        result = await service.delete_flag("nonexistent")

        assert result is False
        service.repository.get_by_name.assert_awaited_once_with("nonexistent")
        service.repository.delete.assert_not_awaited()


class TestFlagsAreDeploymentWide:
    """Flags must be readable and writable from any tenant, or neither.

    `FeatureFlagRepository.get_by_name` goes around `BaseRepository` on
    purpose, so lookups worked from anywhere. The writes still went
    through the tenant filter, so a flag was visible to every tenant but
    editable only from the one that created it. `delete_flag` then
    discarded the repository's answer and reported success anyway, so a
    superuser could evict any flag from Redis, disabling it across the
    whole deployment, while the row survived and the API answered 204.
    """

    @pytest.fixture
    def anyio_backend(self) -> str:
        """Provide the asyncio backend for anyio-marked tests.

        Returns:
            The backend name.
        """
        return "asyncio"

    @pytest.mark.anyio
    async def test_delete_from_another_tenant_really_deletes(
        self, session: AsyncSession
    ) -> None:
        """A reported delete must leave nothing behind.

        Args:
            session: Async database session fixture.
        """
        owner = uuid4()
        stranger = uuid4()
        service = FeatureFlagService(session)

        with tenant_context(owner):
            await service.create_flag(
                FeatureFlagCreate(name="deployment_wide", state=True)
            )

        with tenant_context(stranger):
            with patch(
                "app.services.feature_flag_service.ff.remove_from_redis",
                new_callable=AsyncMock,
            ):
                deleted = await service.delete_flag("deployment_wide")

            assert deleted is True
            # The claim has to hold. It used to be an unconditional True.
            assert await service.get_by_name("deployment_wide") is None

    @pytest.mark.anyio
    async def test_update_from_another_tenant_really_updates(
        self, session: AsyncSession
    ) -> None:
        """A flag created in one tenant must be editable from another.

        Args:
            session: Async database session fixture.
        """
        owner = uuid4()
        stranger = uuid4()
        service = FeatureFlagService(session)

        with tenant_context(owner):
            with patch(
                "app.services.feature_flag_service.ff.refresh_cache",
                new_callable=AsyncMock,
            ):
                await service.create_flag(
                    FeatureFlagCreate(name="editable_anywhere", state=False)
                )

        with tenant_context(stranger):
            with patch(
                "app.services.feature_flag_service.ff.refresh_cache",
                new_callable=AsyncMock,
            ):
                updated = await service.toggle_flag("editable_anywhere", True)

            assert updated is not None
            assert updated.state is True

    @pytest.mark.asyncio
    async def test_delete_reports_false_and_spares_redis_when_nothing_matched(
        self, service: FeatureFlagService
    ) -> None:
        """A delete that matched no row must not evict the cache entry.

        Args:
            service: Feature flag service with a mocked repository.
        """
        service.repository.get_by_name = AsyncMock(return_value=_make_flag())
        service.repository.delete = AsyncMock(return_value=False)

        with patch(
            "app.services.feature_flag_service.ff.remove_from_redis",
            new_callable=AsyncMock,
        ) as mock_remove:
            result = await service.delete_flag("test_flag")

        assert result is False
        mock_remove.assert_not_awaited()


class TestCacheIsInvalidatedNotPublished:
    """A write must not publish an uncommitted state to the shared cache.

    The repository only flushes, the commit happens in the session
    dependency after the route returns. Pushing the new state to Redis and
    to the in-memory cache from inside the service put a value the database
    might never hold in front of every instance, and with no TTL on the key
    it stayed there.
    """

    @pytest.mark.asyncio
    async def test_create_drops_the_cache_entry(
        self, service: FeatureFlagService
    ) -> None:
        """Creating a flag evicts the entry instead of writing the new state.

        Args:
            service: Feature flag service with a mocked repository.
        """
        service.repository.name_exists = AsyncMock(return_value=False)
        service.repository.create = AsyncMock(return_value=_make_flag(state=True))

        with (
            patch(
                "app.services.feature_flag_service.ff.refresh_cache",
                new_callable=AsyncMock,
            ) as mock_refresh,
            patch(
                "app.services.feature_flag_service.ff.sync_to_redis",
                new_callable=AsyncMock,
            ) as mock_sync,
        ):
            await service.create_flag(FeatureFlagCreate(name="test_flag", state=True))

        mock_refresh.assert_awaited_once_with("test_flag")
        mock_sync.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_state_change_drops_the_cache_entry(
        self, service: FeatureFlagService
    ) -> None:
        """Changing state evicts the entry instead of writing the new state.

        Args:
            service: Feature flag service with a mocked repository.
        """
        existing = _make_flag(name="test_flag", state=False)
        service.repository.get_by_name = AsyncMock(return_value=existing)
        service.repository.update = AsyncMock(
            return_value=_make_flag(name="test_flag", state=True)
        )

        with (
            patch(
                "app.services.feature_flag_service.ff.refresh_cache",
                new_callable=AsyncMock,
            ) as mock_refresh,
            patch(
                "app.services.feature_flag_service.ff.sync_to_redis",
                new_callable=AsyncMock,
            ) as mock_sync,
        ):
            await service.toggle_flag("test_flag", True)

        mock_refresh.assert_awaited_once_with("test_flag")
        mock_sync.assert_not_awaited()

    @pytest.mark.anyio
    async def test_rolled_back_create_leaves_no_cached_state(
        self, session: AsyncSession
    ) -> None:
        """A create that never commits must not leave the flag enabled.

        Args:
            session: Async database session fixture.
        """
        from app.core.feature_flags import _in_memory_cache
        from app.core.feature_flags import feature_enabled

        name = f"rolled_back_{uuid4().hex}"
        service = FeatureFlagService(session)

        redis_store: dict[str, str] = {}

        class _FakeRedis:
            async def get(self, key: str) -> str | None:
                return redis_store.get(key)

            async def set(self, key: str, value: str, ex: int | None = None) -> None:
                redis_store[key] = value

            async def delete(self, key: str) -> None:
                redis_store.pop(key, None)

        with patch("app.core.feature_flags._get_redis", return_value=_FakeRedis()):
            with tenant_context(uuid4()):
                await service.create_flag(FeatureFlagCreate(name=name, state=True))

            # The write is flushed but not committed. This is what the
            # session dependency does when anything later in the request
            # raises.
            await session.rollback()

            assert name not in _in_memory_cache
            assert redis_store.get(f"feature_flags:{name}") is None
            assert await feature_enabled(name) is False
