"""User service for user management.

This module provides the UserService class for
user-related operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import SYSTEM_TENANT_ID
from app.core.logging import get_logger
from app.core.security import generate_password_hash
from app.models.audit_log import AuditAction
from app.models.user import User
from app.repositories.audit_repository import AuditLogRepository
from app.repositories.user_repository import UserRepository
from app.schemas.user import UserCreate
from app.schemas.user import UserUpdate
from app.schemas.user import UserUpdateMe

if TYPE_CHECKING:
    pass

logger = get_logger("service.user")


class UserService:
    """Service for user operations.

    Handles user management including creation, updates,
    and profile management.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize user service.

        Args:
            session: Async database session
        """
        self.session = session
        self.user_repository = UserRepository(session)
        self.audit_repository = AuditLogRepository(session)

    async def get_by_id(self, user_id: UUID) -> User | None:
        """Get user by ID.

        Args:
            user_id: User's UUID

        Returns:
            User or None
        """
        return await self.user_repository.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        """Get user by email.

        Args:
            email: User's email

        Returns:
            User or None
        """
        return await self.user_repository.get_by_email(email)

    async def list_users(
        self,
        skip: int = 0,
        limit: int = 20,
        is_active: bool | None = None,
        is_superuser: bool | None = None,
        search: str | None = None,
    ) -> tuple[list[User], int]:
        """List users with filtering.

        Args:
            skip: Pagination offset
            limit: Maximum results
            is_active: Filter by active status
            is_superuser: Filter by admin status
            search: Search in email/name

        Returns:
            Tuple of (users, total_count)
        """
        if search:
            return await self.user_repository.search(search, skip, limit)

        filters = {}
        if is_active is not None:
            filters["is_active"] = is_active
        if is_superuser is not None:
            filters["is_superuser"] = is_superuser

        users, total = await self.user_repository.list(
            skip=skip,
            limit=limit,
            filters=filters if filters else None,
        )
        return list(users), total

    async def create(
        self,
        data: UserCreate,
        created_by: UUID | None = None,
    ) -> User:
        """Create a new user.

        Args:
            data: User creation data
            created_by: UUID of user creating this account

        Returns:
            Created User
        """
        # Check email exists.
        if await self.user_repository.email_exists(data.email):
            raise ValueError(f"Email {data.email} is already registered")

        # Create user.
        password_hash = generate_password_hash(data.password)
        user = await self.user_repository.create_user(
            email=data.email,
            password_hash=password_hash,
            full_name=data.full_name,
            tenant_id=data.tenant_id or SYSTEM_TENANT_ID,
            is_superuser=data.is_superuser,
        )

        # Log creation.
        await self.audit_repository.log_user_action(
            action=AuditAction.USER_CREATE,
            user_id=user.id,
            tenant_id=user.tenant_id or SYSTEM_TENANT_ID,
            email=user.email,
            resource_type="user",
            resource_id=str(user.id),
            metadata={"created_by": str(created_by) if created_by else None},
        )

        logger.info("user_created", user_id=str(user.id), email=user.email)

        return user

    async def update(
        self,
        user_id: UUID,
        data: UserUpdate,
        updated_by: UUID,
    ) -> User | None:
        """Update a user.

        Args:
            user_id: User's UUID
            data: Update data
            updated_by: UUID of user making update

        Returns:
            Updated User or None
        """
        user = await self.user_repository.get(user_id)
        if not user:
            return None

        # Build update dict.
        update_data: dict[str, str | bool] = {}
        if data.email is not None:
            update_data["email"] = data.email
        if data.full_name is not None:
            update_data["full_name"] = data.full_name
        if data.is_active is not None:
            update_data["is_active"] = data.is_active
        if data.is_superuser is not None:
            update_data["is_superuser"] = data.is_superuser

        if update_data:
            updated_user = await self.user_repository.update(
                user_id, update_data.copy()
            )
            if not updated_user:
                return None
            user = updated_user

            # Log update.
            await self.audit_repository.log_user_action(
                action=AuditAction.USER_UPDATE,
                user_id=updated_by,
                tenant_id=user.tenant_id or SYSTEM_TENANT_ID,
                email=user.email,
                resource_type="user",
                resource_id=str(user_id),
                changes=update_data,
            )

        return user

    async def update_profile(
        self,
        user: User,
        data: UserUpdateMe,
    ) -> User:
        """Update own profile.

        Args:
            user: Current user
            data: Update data

        Returns:
            Updated User
        """
        update_data = {}
        if data.email is not None:
            update_data["email"] = data.email
        if data.full_name is not None:
            update_data["full_name"] = data.full_name

        if update_data:
            updated_user = await self.user_repository.update(user.id, update_data)
            if updated_user is None:
                return user

        refreshed_user = await self.user_repository.get(user.id)
        return refreshed_user or user

    async def delete(
        self,
        user_id: UUID,
        deleted_by: UUID,
    ) -> bool:
        """Delete a user (soft delete).

        Args:
            user_id: User's UUID
            deleted_by: UUID of user making deletion

        Returns:
            True if deleted
        """
        user = await self.user_repository.get(user_id)
        if not user:
            return False

        await self.user_repository.delete(user_id)

        # Log deletion.
        await self.audit_repository.log_user_action(
            action=AuditAction.USER_DELETE,
            user_id=deleted_by,
            tenant_id=user.tenant_id or SYSTEM_TENANT_ID,
            email=user.email,
            resource_type="user",
            resource_id=str(user_id),
        )

        logger.info("user_deleted", user_id=str(user_id))

        return True
