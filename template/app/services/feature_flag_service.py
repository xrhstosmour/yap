"""Feature flag service for business logic and state management.

Handles CRUD operations for feature flags and ensures state changes
are immediately synced to Redis for cross-instance propagation.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import feature_flags as ff
from app.core.logging import get_logger
from app.core.tenant import system_context
from app.core.tenant import tenant_context
from app.models.feature_flag import FeatureFlag
from app.repositories.feature_flag_repository import FeatureFlagRepository
from app.schemas.feature_flag import FeatureFlagCreate
from app.schemas.feature_flag import FeatureFlagUpdate

logger = get_logger("service.feature_flag")


class FeatureFlagServiceError(Exception):
    """Base exception for feature flag service operations."""


@contextmanager
def _deployment_scope() -> Iterator[None]:
    """Run a block against every feature flag, regardless of caller tenant.

    Both halves are needed. ``tenant_context(None)`` clears the request's
    tenant, or ``_apply_tenant_filter`` narrows the query to it and never
    consults system access at all, that branch only runs when no tenant is
    set. ``system_context()`` then says the absence is deliberate, or the
    same filter fails closed and raises.

    Yields:
        None.
    """
    with tenant_context(None), system_context():
        yield


class FeatureFlagService:
    """Service for feature flag operations.

    Manages CRUD and ensures state propagation via Redis sync.

    Every database call here runs inside ``_deployment_scope()``, because a
    feature flag is deployment-wide, not per tenant. Three things in the
    design say so and none of them can be reconciled with tenant scoping:
    ``FeatureFlag.name`` is globally unique, ``feature_enabled()`` takes a
    bare name with no tenant, and both of its caches are keyed on that name
    alone (``feature_flags:<name>`` in Redis, plus a process-local dict).

    Reads were already unscoped, ``FeatureFlagRepository.get_by_name`` goes
    around ``BaseRepository`` deliberately, since ``feature_enabled()``
    runs from background tasks with no request and therefore no tenant
    context, where the fail-closed filter would raise. The writes were not,
    so a flag was visible to everyone and editable only from the tenant
    that happened to create it. That mismatch is what made ``delete_flag``
    able to report success while deleting nothing.
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
        with _deployment_scope():
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
        with _deployment_scope():
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

        # Lands on `SYSTEM_TENANT_ID`, the fallback `BaseRepository.create`
        # applies when there is no tenant in context. Created under the
        # caller's own tenant instead, the flag was reachable by name from
        # every tenant but editable only from that one.
        with _deployment_scope():
            flag = await self.repository.create(
                {
                    "name": data.name,
                    "state": data.state,
                    "description": data.description,
                }
            )

        # Invalidate rather than publish. The repository has only flushed at
        # this point, the commit happens after the route returns, so pushing
        # `data.state` here would put a state the database may never hold in
        # front of every instance. Dropping the entry costs one database read
        # on the next lookup and is correct whichever way the transaction goes.
        await ff.refresh_cache(data.name)
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
            with _deployment_scope():
                updated = await self.repository.update(flag.id, update_data)
            if updated is None:
                return None
            if data.state is not None:
                await ff.refresh_cache(name)
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

        The repository's own answer is returned rather than an
        unconditional ``True``. Discarding it meant the caller was told the
        flag was gone whenever the lookup above found one, even though the
        delete itself had matched nothing, and the flag was evicted from
        Redis regardless. A superuser could therefore turn off any flag
        their tenant did not own: the row survived, the cache entry did
        not, and the API answered 204.

        Args:
            name: Feature flag name

        Returns:
            True if deleted, False if not found
        """
        flag = await self.repository.get_by_name(name)
        if not flag:
            return False

        with _deployment_scope():
            deleted = await self.repository.delete(flag.id)
        if not deleted:
            return False

        await ff.remove_from_redis(name)
        logger.info("feature_flag_deleted", name=name)
        return True
