"""Feature flag repository for database operations."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import and_
from sqlmodel import select

from app.core.logging import get_logger
from app.models.feature_flag import FeatureFlag
from app.repositories.base import BaseRepository

logger = get_logger("repository.feature_flag")


class FeatureFlagRepository(BaseRepository[FeatureFlag]):
    """Repository for FeatureFlag model operations."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize feature flag repository.

        Args:
            session: Async database session
        """
        super().__init__(session, FeatureFlag)

    async def get_by_name(self, name: str) -> FeatureFlag | None:
        """Get feature flag by its unique name.

        Ignores soft-deleted rows: a deleted flag must not keep
        evaluating as active for `feature_enabled()` callers, and its
        name must be free to reuse for a new flag.

        Args:
            name: Feature flag name

        Returns:
            FeatureFlag instance or None
        """
        query = select(FeatureFlag).where(
            and_(
                FeatureFlag.name == name,  # type: ignore[arg-type]
                FeatureFlag.deleted_at.is_(None),  # type: ignore[union-attr]
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def name_exists(self, name: str) -> bool:
        """Check if a feature flag name already exists.

        Args:
            name: Feature flag name

        Returns:
            True if name is taken
        """
        existing = await self.get_by_name(name)
        return existing is not None

    async def list_active(self) -> list[FeatureFlag]:
        """List all active (non-deleted) feature flags.

        Returns:
            List of active feature flags
        """
        flags, _ = await self.list(limit=1000)
        return list(flags)
