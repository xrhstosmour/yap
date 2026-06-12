"""OAuth account repository for multi-provider social login.

This module provides the OAuthAccountRepository class for
OAuth account database operations.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.oauth_account import OAuthAccount
from app.repositories.base import BaseRepository

logger = get_logger("repository.oauth_account")


class OAuthAccountRepository(BaseRepository[OAuthAccount]):
    """Repository for OAuthAccount model operations.

    Provides OAuth-specific database operations for looking up
    and linking provider accounts to local users.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize OAuth account repository.

        Args:
            session: Async database session
        """
        super().__init__(session, OAuthAccount)

    async def get_by_provider(
        self,
        provider: str,
        provider_user_id: str,
    ) -> OAuthAccount | None:
        """Get linked OAuth account by provider and subject ID.

        Args:
            provider: OAuth provider name (e.g. "google", "apple").
            provider_user_id: Subject/sub identifier from the provider.

        Returns:
            OAuthAccount with its user loaded via selectin, or None.
        """
        query = select(OAuthAccount).where(
            and_(
                OAuthAccount.provider == provider,  # type: ignore[arg-type]
                OAuthAccount.provider_user_id == provider_user_id,  # type: ignore[arg-type]
                OAuthAccount.deleted_at.is_(None),  # type: ignore[union-attr]
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create_link(
        self,
        user_id: UUID,
        provider: str,
        provider_user_id: str,
        provider_email: str | None = None,
        tenant_id: UUID | None = None,
    ) -> OAuthAccount:
        """Link an OAuth provider account to a local user.

        Args:
            user_id: UUID of the local user to link.
            provider: OAuth provider name (e.g. "google", "apple").
            provider_user_id: Subject/sub identifier from the provider.
            provider_email: Email address returned by the provider.
            tenant_id: Tenant to assign the linked account to.

        Returns:
            Created OAuthAccount instance.
        """
        return await self.create(
            {
                "user_id": user_id,
                "provider": provider,
                "provider_user_id": provider_user_id,
                "provider_email": provider_email,
                "tenant_id": tenant_id,
            }
        )

    async def get_user_accounts(self, user_id: UUID) -> list[OAuthAccount]:
        """Get all linked OAuth accounts for a user.

        Args:
            user_id: UUID of the user.

        Returns:
            List of linked OAuthAccount instances.
        """
        query = select(OAuthAccount).where(
            and_(
                OAuthAccount.user_id == user_id,  # type: ignore[arg-type]
                OAuthAccount.deleted_at.is_(None),  # type: ignore[union-attr]
            )
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())
