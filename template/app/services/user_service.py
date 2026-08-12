"""User service for user management.

This module provides the UserService class for
user-related operations.
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import SYSTEM_TENANT_ID
from app.core.logging import get_logger
from app.core.security import generate_password_hash
from app.core.security import verify_password
from app.models.api_key import APIKey
from app.models.audit_log import AuditAction
from app.models.totp_recovery_code import TotpRecoveryCode
from app.models.user import User
from app.models.user import UserRole
from app.models.webauthn_credential import WebAuthnCredential
from app.repositories.audit_repository import AuditLogRepository
from app.repositories.user_repository import UserRepository
from app.schemas.user import UserCreate
from app.schemas.user import UserUpdate
from app.schemas.user import UserUpdateMe

logger = get_logger("service.user")


class UserServiceError(Exception):
    """Base exception for user service errors."""

    pass


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
        role: UserRole | None = None,
        search: str | None = None,
    ) -> tuple[list[User], int]:
        """List users with filtering.

        Args:
            skip: Pagination offset
            limit: Maximum results
            is_active: Filter by active status
            role: Filter by user role
            search: Search in email/name

        Returns:
            Tuple of (users, total_count)
        """
        filters: dict[str, object] = {}
        if is_active is not None:
            filters["is_active"] = is_active
        if role is not None:
            filters["role"] = role

        if search:
            return await self.user_repository.search(
                search,
                skip=skip,
                limit=limit,
            )

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
            raise UserServiceError("Email already in use")

        # Create user.
        role = UserRole(data.role) if data.role else UserRole.USER
        password_hash = await asyncio.to_thread(generate_password_hash, data.password)
        user = await self.user_repository.create_user(
            email=data.email,
            password_hash=password_hash,
            full_name=data.full_name,
            tenant_id=data.tenant_id or SYSTEM_TENANT_ID,
            role=role,
        )

        # Log creation.
        await self.audit_repository.log_user_action_safe(
            action=AuditAction.USER_CREATE,
            user_id=user.id,
            tenant_id=user.tenant_id or SYSTEM_TENANT_ID,
            email=user.email,
            resource_type="user",
            resource_id=str(user.id),
            metadata={"created_by": str(created_by) if created_by else None},
        )

        logger.info("user_created", user_id=str(user.id))

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
        if data.role is not None:
            update_data["role"] = data.role

        # Check for email conflicts if email is being changed.
        email = update_data.get("email")
        if isinstance(email, str) and email != user.email:
            existing = await self.user_repository.get_by_email(email)
            if existing:
                raise UserServiceError("Email already in use")

        if update_data:
            updated_user = await self.user_repository.update(user_id, update_data)
            if not updated_user:
                return None
            user = updated_user

            # Log update.
            await self.audit_repository.log_user_action_safe(
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

        Supports updating name, email, phone, and password with proper
        verification. When email changes or a new password is set, the
        current_password must be provided and verified.

        Args:
            user: Current user
            data: Update data

        Returns:
            Updated User

        Raises:
            UserServiceError: On validation failure or email conflict.
        """
        update_data: dict[str, object] = {}
        if data.email is not None:
            update_data["email"] = data.email
        if data.full_name is not None:
            update_data["full_name"] = data.full_name
        if "phone" in data.model_fields_set:
            update_data["phone"] = data.phone

        email_changing = isinstance(data.email, str) and data.email != user.email
        password_changing = data.new_password is not None

        # Require current_password for email change or password change.
        if email_changing or password_changing:
            if not data.current_password:
                raise UserServiceError("Current password is required for this change")
            if not await asyncio.to_thread(
                verify_password, data.current_password, user.hashed_password
            ):
                raise UserServiceError("Current password is incorrect")

        # Check email uniqueness before updating.
        if email_changing:
            existing = await self.user_repository.get_by_email(data.email)  # type: ignore[arg-type]
            if existing:
                raise UserServiceError("Email already in use")

        # Hash new password if provided.
        if password_changing:
            update_data["hashed_password"] = await asyncio.to_thread(
                generate_password_hash,
                data.new_password,  # type: ignore[arg-type]
            )
            # `User.token_version + 1` (the class attribute, a SQL expression)
            # rather than `user.token_version + 1` (the loaded instance's
            # possibly-stale Python value): a concurrent request bumping the
            # same counter from the same starting value would otherwise lose
            # one of the two increments, and token_version exists specifically
            # to invalidate every outstanding session on a password change, so
            # an under-count there is a stale-token bug, not just a cosmetic one.
            update_data["token_version"] = User.token_version + 1

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
        await self.audit_repository.log_user_action_safe(
            action=AuditAction.USER_DELETE,
            user_id=deleted_by,
            tenant_id=user.tenant_id or SYSTEM_TENANT_ID,
            email=user.email,
            resource_type="user",
            resource_id=str(user_id),
        )

        logger.info("user_deleted", user_id=str(user_id))

        return True

    async def delete_me(self, user: User) -> None:
        """Self-service account deletion (GDPR Article 17).

        Revokes all API keys, anonymizes personal fields (email, phone,
        full_name, hashed_password), clears 2FA (TOTP secret, recovery
        codes, WebAuthn credentials), soft-deletes the account, and
        increments `token_version` to immediately invalidate all
        outstanding JWTs. Logs the deletion in the audit trail.

        Args:
            user: The authenticated user requesting deletion.
        """
        tenant_id = user.tenant_id or SYSTEM_TENANT_ID
        now = datetime.now(UTC)

        # Wrap deletion in a savepoint so API key revocation and
        # anonymization are atomic — if either fails, both roll back.
        async with self.session.begin_nested():
            # Revoke all active API keys so key-based auth stops working immediately.
            await self.session.execute(
                sa_update(APIKey)
                .where(
                    APIKey.user_id == user.id,  # type: ignore[arg-type]
                    APIKey.deleted_at.is_(None),  # type: ignore[union-attr]
                )
                .values(deleted_at=now)
            )

            # Hard-delete 2FA credential material. This is not soft-deleted
            # like the account itself, an erasure request means it should
            # stop existing, not just stop being queried.
            await self.session.execute(
                sa_delete(TotpRecoveryCode).where(
                    TotpRecoveryCode.user_id == user.id  # type: ignore[arg-type]
                )
            )
            await self.session.execute(
                sa_delete(WebAuthnCredential).where(
                    WebAuthnCredential.user_id == user.id  # type: ignore[arg-type]
                )
            )

            # Anonymize personal fields to satisfy GDPR Article 17 right to erasure.
            # `phone`/`email` reassignment here goes through `User.update()`,
            # which sets attributes on a loaded instance rather than issuing a
            # bare SQL UPDATE, so the `phone_hash`/`email_hash` sync listeners
            # still fire and those hashes get anonymized too, not just the
            # plaintext columns.
            anonymized_email = f"deleted_{user.id}@deleted.invalid"
            placeholder_hash = await asyncio.to_thread(
                generate_password_hash, secrets.token_urlsafe(32)
            )
            await self.user_repository.update(
                user.id,
                {
                    "email": anonymized_email,
                    "phone": None,
                    "full_name": None,
                    "hashed_password": placeholder_hash,
                    "is_2fa_enabled": False,
                    "totp_secret_encrypted": None,
                    "totp_confirmed_at": None,
                    "token_version": User.token_version + 1,
                },
            )

            # Soft-delete the record (token already bumped above).
            await self.user_repository.delete(user.id)

        await self.audit_repository.log_user_action_safe(
            action=AuditAction.ACCOUNT_DELETION,
            user_id=user.id,
            tenant_id=tenant_id,
            email=user.email,
            resource_type="user",
            resource_id=str(user.id),
        )

        logger.info("account_self_deleted", user_id=str(user.id))

    async def revoke_all_sessions(
        self,
        user_id: UUID,
        revoked_by: User,
    ) -> bool:
        """Revoke all active JWT sessions for target user.

        Args:
            user_id: User whose sessions are revoked.
            revoked_by: Superuser performing revocation.

        Returns:
            True when user exists and revocation succeeded.
        """
        user = await self.user_repository.get(user_id)
        if not user:
            return False

        await self.user_repository.increment_token_version(user_id)

        tenant_id = user.tenant_id or SYSTEM_TENANT_ID
        await self.audit_repository.log_user_action_safe(
            action=AuditAction.SESSION_REVOKE,
            user_id=revoked_by.id,
            tenant_id=tenant_id,
            email=revoked_by.email,
            resource_type="user",
            resource_id=str(user_id),
            metadata={"revoked_for": str(user_id)},
        )

        logger.info(
            "user_sessions_revoked",
            user_id=str(user_id),
            revoked_by=str(revoked_by.id),
        )

        return True

    async def export_my_data(self, user: User) -> dict[str, Any]:
        """Export all personal data for the user (GDPR Article 20).

        Compiles profile information, API key metadata (no secrets),
        and audit activity into a single portable JSON-serializable
        dict. Logs the export action for compliance.

        Args:
            user: The authenticated user requesting their data.

        Returns:
            JSON-serializable dict containing the user's personal data.
        """
        from app.repositories.api_key_repository import APIKeyRepository

        tenant_id = user.tenant_id or SYSTEM_TENANT_ID

        # Fetch audit activity for this user (up to 500 most recent entries).
        activity_logs, _ = await self.audit_repository.get_by_actor(
            actor_id=str(user.id), skip=0, limit=500
        )

        profile: dict[str, Any] = {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "is_active": user.is_active,
            "is_verified": user.is_verified,
            "created_at": user.created_at.isoformat(),
            "updated_at": user.updated_at.isoformat(),
        }

        # Explicitly load API keys since the relationship may not be
        # loaded through the current_user dependency.
        api_key_repo = APIKeyRepository(self.session)
        keys, _ = await api_key_repo.list(
            filters={"user_id": user.id},
        )
        api_keys = [
            {
                "name": k.name,
                "description": k.description,
                "scopes": k.scopes,
                "is_active": k.is_active,
                "created_at": k.created_at.isoformat(),
                "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                "expires_at": k.expires_at.isoformat() if k.expires_at else None,
            }
            for k in keys
        ]

        activity = [
            {
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "status": log.status,
                "created_at": log.created_at.isoformat(),
            }
            for log in activity_logs
        ]

        await self.audit_repository.log_user_action_safe(
            action=AuditAction.DATA_EXPORT,
            user_id=user.id,
            tenant_id=tenant_id,
            email=user.email,
            resource_type="user",
            resource_id=str(user.id),
        )

        logger.info("data_exported", user_id=str(user.id))

        return {
            "exported_at": datetime.now(UTC).isoformat(),
            "profile": profile,
            "api_keys": api_keys,
            "activity": activity,
        }
