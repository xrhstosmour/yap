"""User repository for database operations.

This module provides the UserRepository class for
user-related database operations.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import and_
from sqlmodel import func
from sqlmodel import select

from app.core.logging import get_logger
from app.models.user import User
from app.repositories.base import BaseRepository
from app.repositories.mixins import SearchMixin

logger = get_logger("repository.user")


class UserRepository(SearchMixin, BaseRepository[User]):
    """Repository for User model operations.

    Provides user-specific database operations beyond basic CRUD,
    including authentication helpers and email lookups.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize user repository.

        Args:
            session: Async database session
        """
        super().__init__(session, User)

    async def get_by_email(self, email: str) -> User | None:
        """Get user by email address.

        Args:
            email: User's email address

        Returns:
            User instance or None if not found
        """
        query = select(User).where(
            and_(
                User.email == email,  # type: ignore[arg-type]
                User.deleted_at.is_(None),  # type: ignore[union-attr]
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def email_exists(self, email: str) -> bool:
        """Check if email is already registered.

        Args:
            email: Email to check

        Returns:
            True if email exists
        """
        query = select(func.count()).select_from(User).where(User.email == email)  # type: ignore[arg-type]
        result = await self.session.execute(query)
        return (result.scalar() or 0) > 0

    async def search(
        self,
        query_str: str,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[list[User], int]:
        """Search users by email or name.

        Args:
            query_str: Search query
            skip: Pagination offset
            limit: Maximum results

        Returns:
            Tuple of (users, total_count)
        """
        users, total = await self.search_combined(
            query_str=query_str,
            fields=["email", "full_name"],
            skip=skip,
            limit=limit,
        )

        return users, total

    async def create_user(
        self,
        email: str,
        password_hash: str,
        full_name: str | None = None,
        tenant_id: UUID | None = None,
        is_superuser: bool = False,
        is_verified: bool = False,
    ) -> User:
        """Create a new user with password.

        Args:
            email: User's email address.
            password_hash: Bcrypt hash of password (or random bytes for OAuth).
            full_name: Optional display name.
            tenant_id: Optional tenant ID.
            is_superuser: Whether user is admin.
            is_verified: Whether email is pre-verified (e.g. OAuth accounts).

        Returns:
            Created User instance.
        """
        return await self.create(
            {
                "email": email,
                "hashed_password": password_hash,
                "full_name": full_name,
                "tenant_id": tenant_id,
                "is_superuser": is_superuser,
                "is_active": True,
                "is_verified": is_verified,
            }
        )

    async def update_password(self, user_id: UUID | str, new_hash: str) -> User | None:
        """Update user's password hash.

        Args:
            user_id: User's UUID
            new_hash: New bcrypt hash

        Returns:
            Updated User or None if not found
        """
        return await self.update(user_id, {"hashed_password": new_hash})

    async def set_verified(self, user_id: UUID | str) -> User | None:
        """Mark user's email as verified.

        Args:
            user_id: User's UUID

        Returns:
            Updated User or None if not found
        """
        return await self.update(user_id, {"is_verified": True})

    async def set_active(self, user_id: UUID | str, is_active: bool) -> User | None:
        """Set user's active status.

        Args:
            user_id: User's UUID
            is_active: New active status

        Returns:
            Updated User or None if not found
        """
        return await self.update(user_id, {"is_active": is_active})

    async def increment_token_version(self, user_id: UUID | str) -> None:
        """Increment token version for user.

        Args:
            user_id: User's UUID
        """
        statement = (
            update(User)
            .where(User.id == user_id)  # type: ignore[arg-type]
            .values(token_version=User.token_version + 1)
        )
        await self.session.execute(statement)
        await self.session.flush()
