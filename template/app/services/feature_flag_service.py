"""Feature flag service for business logic and state management.

Handles CRUD operations for feature flags and ensures state changes
are immediately synced to Redis for cross-instance propagation.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import feature_flags as ff
from app.core.logging import get_logger
from app.models.feature_flag import FeatureFlag
from app.repositories.feature_flag_repository import FeatureFlagRepository
from app.schemas.feature_flag import FeatureFlagCreate
from app.schemas.feature_flag import FeatureFlagUpdate

logger = get_logger("service.feature_flag")


class FeatureFlagServiceError(Exception):
    """Base exception for feature flag service operations."""


class FeatureFlagService:
    """Service for feature flag operations.

    Manages CRUD and ensures state propagation via Redis sync.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize feature flag service.

        Args:
            session: Async database session
        """
        self.session = session
        self.repository = FeatureFlagRepository(session)

    async def get_by_name(self, name: str) -> FeatureFlag | None:
        """Get a feature flag by name.

        Args:
            name: Feature flag name

        Returns:
            FeatureFlag or None
        """
        return await self.repository.get_by_name(name)

    async def get_by_id(self, flag_id: UUID) -> FeatureFlag | None:
        """Get a feature flag by ID.

        Args:
            flag_id: Feature flag UUID

        Returns:
            FeatureFlag or None
        """
        return await self.repository.get(flag_id)

    async def list_flags(
        self,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[list[FeatureFlag], int]:
        """List feature flags with pagination.

        Args:
            skip: Pagination offset
            limit: Maximum results

        Returns:
            Tuple of (flags, total_count)
        """
        flags, total = await self.repository.list(skip=skip, limit=limit)
        return list(flags), total

    async def create_flag(self, data: FeatureFlagCreate) -> FeatureFlag:
        """Create a new feature flag.

        Args:
            data: Creation data

        Returns:
            Created FeatureFlag

        Raises:
            FeatureFlagServiceError: If name already exists
        """
        if await self.repository.name_exists(data.name):
            raise FeatureFlagServiceError(f"Feature flag '{data.name}' already exists")

        flag = await self.repository.create(
            {
                "name": data.name,
                "state": data.state,
                "description": data.description,
            }
        )

        await ff.sync_to_redis(data.name, data.state)
        logger.info("feature_flag_created", name=data.name, state=data.state)
        return flag

    async def update_flag(
        self, name: str, data: FeatureFlagUpdate
    ) -> FeatureFlag | None:
        """Update a feature flag.

        Args:
            name: Feature flag name
            data: Update data

        Returns:
            Updated FeatureFlag or None
        """
        flag = await self.repository.get_by_name(name)
        if not flag:
            return None

        update_data: dict[str, bool | str] = {}
        if data.state is not None:
            update_data["state"] = data.state
        if data.description is not None:
            update_data["description"] = data.description

        if update_data:
            updated = await self.repository.update(flag.id, update_data)
            if updated and data.state is not None:
                await ff.sync_to_redis(name, data.state)
            logger.info("feature_flag_updated", name=name, changes=update_data)
            return updated

        return flag

    async def toggle_flag(self, name: str, state: bool) -> FeatureFlag | None:
        """Toggle a feature flag to a specific state.

        Args:
            name: Feature flag name
            state: Target state

        Returns:
            Updated FeatureFlag or None
        """
        return await self.update_flag(name, FeatureFlagUpdate(state=state))

    async def delete_flag(self, name: str) -> bool:
        """Delete a feature flag (soft delete).

        Args:
            name: Feature flag name

        Returns:
            True if deleted, False if not found
        """
        flag = await self.repository.get_by_name(name)
        if not flag:
            return False

        await self.repository.delete(flag.id)
        await ff.remove_from_redis(name)
        logger.info("feature_flag_deleted", name=name)
        return True
