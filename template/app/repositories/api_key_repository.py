"""API Key repository for database operations.

This module provides the APIKeyRepository class for
API key-related database operations.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import Any
from typing import cast
from uuid import UUID

from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import and_
from sqlmodel import func
from sqlmodel import select

from app.core.logging import get_logger
from app.core.security import verify_password
from app.models.api_key import APIKey
from app.repositories.base import BaseRepository

logger = get_logger("repository.api_key")


class APIKeyRepository(BaseRepository[APIKey]):
    """Repository for APIKey model operations.

    Provides API key-specific database operations including
    key verification and usage tracking.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize API key repository.

        Args:
            session: Async database session
        """
        super().__init__(session, APIKey)

    async def get_by_key_id(self, key_id: str) -> APIKey | None:
        """Get API key by its public identifier, deliberately unscoped.

        Runs before the caller knows which tenant the key belongs to, this
        is what resolves that tenant, the same way `TenantRepository.get_by_slug`
        looks up cross-tenant on purpose. Safe because `key_id` is globally
        unique (see the model's `unique=True` index), not a guessable
        sequential value another tenant's key could collide with.

        Args:
            key_id: Public key identifier

        Returns:
            APIKey instance or None
        """
        query = select(APIKey).where(APIKey.key_id == key_id)  # type: ignore[arg-type]
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def verify_key(self, key_id: str, key_secret: str) -> APIKey | None:
        """Verify API key and return if valid.

        Args:
            key_id: Public key identifier
            key_hash: Hash of the provided key

        Returns:
            APIKey if valid, None otherwise
        """
        api_key = await self.get_by_key_id(key_id)

        if not api_key:
            return None

        if not verify_password(key_secret, api_key.key_hash):
            return None

        if not api_key.is_valid():
            return None

        return api_key

    async def update_last_used(self, key_id: str | UUID) -> None:
        """Update last_used_at timestamp.

        Args:
            key_id: API key ID
        """
        api_key = await self.get(key_id)
        if api_key:
            api_key.last_used_at = datetime.now(UTC)
            await self.session.flush()

    async def revoke(self, key_id: str | UUID) -> APIKey | None:
        """Revoke an API key.

        Args:
            key_id: API key ID

        Returns:
            Updated APIKey or None
        """
        return await self.update(key_id, {"is_active": False})

    async def list_by_user(
        self,
        user_id: str | UUID,
        skip: int = 0,
        limit: int = 20,
        include_inactive: bool = False,
    ) -> tuple[list[APIKey], int]:
        """List API keys for a user.

        Args:
            user_id: User's UUID
            skip: Pagination offset
            limit: Maximum results
            include_inactive: Include revoked keys

        Returns:
            Tuple of (keys, total_count)
        """
        filters: dict[str, Any] = {"user_id": user_id}

        if not include_inactive:
            filters["is_active"] = True

        keys, total = await self.list(
            skip=skip,
            limit=limit,
            filters=filters,
        )
        return list(keys), total

    async def count_active_by_user(self, user_id: str | UUID) -> int:
        """Count active API keys for a user.

        Args:
            user_id: User's UUID

        Returns:
            Number of active keys

        Raises:
            TenantContextRequiredError: If no tenant context is set and
                `system_context()` is not active.
        """
        query = (
            select(func.count())
            .select_from(APIKey)
            .where(
                and_(
                    APIKey.user_id == user_id,  # type: ignore[arg-type]
                    APIKey.is_active,  # type: ignore[arg-type]
                    APIKey.deleted_at.is_(None),  # type: ignore[union-attr]
                )
            )
        )
        query = self._apply_tenant_filter(query)
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def deactivate_expired_keys(self) -> int:
        """Deactivate all API keys that have passed their expiration date.

        Returns:
            Count of keys deactivated
        """
        from sqlalchemy import update

        now = datetime.now(UTC)

        statement = (
            update(APIKey)
            .where(
                and_(
                    APIKey.expires_at.is_not(None),  # type: ignore[union-attr]
                    APIKey.expires_at <= now,  # type: ignore[arg-type,operator]
                    APIKey.is_active,  # type: ignore[arg-type]
                    APIKey.deleted_at.is_(None),  # type: ignore[union-attr]
                )
            )
            .values(is_active=False, updated_at=now)
        )

        result = cast(CursorResult[Any], await self.session.execute(statement))
        await self.session.flush()

        row_count = result.rowcount or 0

        logger.info("expired_api_keys_deactivated", count=row_count)
        return row_count
