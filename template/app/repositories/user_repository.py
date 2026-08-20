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

from app.core.encryption import crypto
from app.core.logging import get_logger
from app.core.tenant import get_current_tenant_id
from app.models.user import User
from app.models.user import UserRole
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

        `email` is encrypted at rest, so lookups filter on `email_hash`
        (a deterministic HMAC of the address) rather than the encrypted
        column itself, which cannot be compared directly in SQL. Matches
        against a hash candidate for every configured key, not just the
        primary one, so a rotation doesn't strand rows hashed under an
        older key, see `CryptoService.hash_candidates_for_search()`.

        Args:
            email: User's email address

        Returns:
            User instance or None if not found
        """
        query = select(User).where(
            and_(
                User.email_hash.in_(  # type: ignore[arg-type,attr-defined]
                    crypto.hash_candidates_for_search(email)
                ),
                User.deleted_at.is_(None),  # type: ignore[union-attr]
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def email_exists(self, email: str) -> bool:
        """Check if email is already registered.

        Ignores soft-deleted rows, matching `get_by_email`. Otherwise a
        user who self-deletes their account (Article 17 erasure) could
        never re-register with the same address: `email_exists` would
        keep reporting it taken while `get_by_email` reports no user,
        blocking both registration and login. Matches against a hash
        candidate for every configured key, see `get_by_email`.

        Args:
            email: Email to check

        Returns:
            True if email exists
        """
        query = (
            select(func.count())
            .select_from(User)
            .where(
                and_(
                    User.email_hash.in_(  # type: ignore[arg-type,attr-defined]
                        crypto.hash_candidates_for_search(email)
                    ),
                    User.deleted_at.is_(None),  # type: ignore[union-attr]
                )
            )
        )
        result = await self.session.execute(query)
        return (result.scalar() or 0) > 0

    async def search(
        self,
        query_str: str,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[list[User], int]:
        """Search users by name.

        `email` is encrypted at rest (Fernet ciphertext is randomised
        per value), so it cannot support trigram/full-text matching and
        is intentionally excluded here, only exact-match lookups via
        `email_hash` are possible on it (see `get_by_email`). Only
        `full_name`, which is stored unencrypted for this reason, is
        searched.

        Args:
            query_str: Search query
            skip: Pagination offset
            limit: Maximum results

        Returns:
            Tuple of (users, total_count)
        """
        users, total = await self.search_combined(
            query_str=query_str,
            fields=["full_name"],
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
        role: UserRole = UserRole.USER,
        is_verified: bool = False,
    ) -> User:
        """Create a new user with password.

        Args:
            email: User's email address.
            password_hash: Bcrypt hash of password (or random bytes for OAuth).
            full_name: Optional display name.
            tenant_id: Optional tenant ID.
            role: User role for access control.
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
                "role": role,
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

        Scoped to the active tenant and to non-deleted rows, matching
        every other write in this repository, so a caller that reaches
        this with an unvalidated ID cannot invalidate another tenant's
        user's sessions.

        Args:
            user_id: User's UUID
        """
        tenant_id = get_current_tenant_id()
        conditions = [User.id == user_id, User.deleted_at.is_(None)]  # type: ignore[arg-type,union-attr]
        if tenant_id is not None:
            conditions.append(User.tenant_id == tenant_id)  # type: ignore[arg-type]

        statement = (
            update(User)
            .where(and_(*conditions))
            .values(token_version=User.token_version + 1)
        )
        await self.session.execute(statement)
        await self.session.flush()
