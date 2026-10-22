"""API Key service for managing API keys.

This module provides the APIKeyService class for creating
and managing API keys.
"""

from __future__ import annotations

import asyncio
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import DUMMY_API_KEY_HASH
from app.core.security import generate_api_key
from app.core.security import generate_api_key_id
from app.core.security import hash_api_key
from app.core.security import is_legacy_api_key_hash
from app.core.security import verify_api_key
from app.core.security import verify_password
from app.models.api_key import APIKey
from app.models.audit_log import AuditAction
from app.repositories.api_key_repository import APIKeyRepository
from app.repositories.audit_repository import AuditLogRepository
from app.repositories.user_repository import UserRepository
from app.schemas.api_key import APIKeyCreate
from app.schemas.api_key import APIKeyUpdate

logger = get_logger("service.api_key")

# How often a key in use has its `last_used_at` written.
LAST_USED_INTERVAL = timedelta(minutes=5)


def _is_stale(last_used_at: datetime | None) -> bool:
    """Whether `last_used_at` is old enough to be written again.

    Args:
        last_used_at: The stored time, naive UTC from the database.

    Returns:
        True when never set or older than `LAST_USED_INTERVAL`.
    """
    if last_used_at is None:
        return True
    if last_used_at.tzinfo is None:
        last_used_at = last_used_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - last_used_at >= LAST_USED_INTERVAL


class APIKeyService:
    """Service for API key operations.

    Handles API key creation, verification, and management.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize API key service.

        Args:
            session: Async database session
        """
        self.session = session
        self.apikey_repository = APIKeyRepository(session)
        self.user_repository = UserRepository(session)
        self.audit_repository = AuditLogRepository(session)

    async def create(
        self,
        user_id: UUID,
        tenant_id: UUID,
        data: APIKeyCreate,
        user_email: str | None = None,
    ) -> tuple[APIKey, str]:
        """Create a new API key.

        Args:
            user_id: User creating the key
            tenant_id: Tenant for the key
            data: API key creation data
            user_email: Pre-fetched user email for audit logging (avoids N+1)

        Returns:
            Tuple of (APIKey, raw_key) where raw_key is shown only once
        """
        # Generate key.
        full_key, hashed_key = generate_api_key()
        key_id = generate_api_key_id()

        # Validate expiration bounds.
        if data.expires_in_days is not None:
            if data.expires_in_days < 1 or data.expires_in_days > 365:
                raise ValueError("expires_in_days must be between 1 and 365")

        # Calculate expiration.
        expires_at = None
        if data.expires_in_days:
            expires_at = datetime.now(UTC) + timedelta(days=data.expires_in_days)

        # Create API key.
        # NOTE: key_prefix stores the first 12 chars of the raw secret for
        # identification (Stripe-style). The full key is never stored; only a
        # SHA-256 hash is persisted. The prefix allows users to recognize their
        # keys in listings without exposing the full secret.
        api_key = await self.apikey_repository.create(
            {
                "key_id": key_id,
                "key_hash": hashed_key,
                "key_prefix": full_key[:12],
                "name": data.name,
                "description": data.description,
                "scopes": data.scopes,
                "is_active": True,
                "expires_at": expires_at,
                "tenant_id": tenant_id,
                "user_id": user_id,
            }
        )

        # Log creation.
        await self.audit_repository.log_user_action_safe(
            action=AuditAction.APIKEY_CREATE,
            user_id=user_id,
            tenant_id=tenant_id,
            email=user_email,
            resource_type="api_key",
            resource_id=key_id,
            metadata={"name": data.name},
        )

        logger.info("api_key_created", key_id=key_id, user_id=str(user_id))

        return api_key, full_key

    async def verify(
        self,
        key_id: str,
        provided_key: str,
    ) -> APIKey | None:
        """Verify an API key.

        Args:
            key_id: Public key identifier
            provided_key: The actual API key

        Returns:
            APIKey if valid, None otherwise
        """
        # Get key from database.
        api_key = await self.apikey_repository.get_by_key_id(key_id)

        if not api_key:
            # Compare anyway, so response time does not tell an anonymous
            # caller which key IDs exist.
            verify_api_key(provided_key, DUMMY_API_KEY_HASH)
            return None

        # Revoked, expired or deleted keys stop here, before any bcrypt.
        if not api_key.is_valid():
            return None

        if is_legacy_api_key_hash(api_key.key_hash):
            # A key created before SHA-256 hashing still verifies with bcrypt
            # once, and is moved to the fast hash on that use.
            if not await asyncio.to_thread(
                verify_password, provided_key, api_key.key_hash
            ):
                return None
            await self.apikey_repository.update_key_hash(
                api_key.id, hash_api_key(provided_key)
            )
        elif not verify_api_key(provided_key, api_key.key_hash):
            return None

        # Stamped at most once per interval. Writing it on every request made
        # each one a row update, and concurrent requests on one key queue on
        # that row's lock.
        if _is_stale(api_key.last_used_at):
            await self.apikey_repository.update_last_used(api_key.id)

        return api_key

    async def list_for_user(
        self,
        user_id: UUID,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[list[APIKey], int]:
        """List API keys for a user.

        Args:
            user_id: User's UUID
            skip: Pagination offset
            limit: Maximum results

        Returns:
            Tuple of (keys, total_count)
        """
        return await self.apikey_repository.list_by_user(
            user_id=user_id,
            skip=skip,
            limit=limit,
        )

    async def update(
        self,
        key_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
        data: APIKeyUpdate,
        user_email: str | None = None,
    ) -> APIKey | None:
        """Update an API key.

        Args:
            key_id: API key ID
            user_id: User making the update
            tenant_id: Tenant context
            data: Update data
            user_email: Pre-fetched user email for audit logging (avoids N+1)

        Returns:
            Updated APIKey or None
        """
        api_key = await self.apikey_repository.get(key_id)
        if not api_key or api_key.user_id != user_id:
            return None

        # Build update dict.
        update_data: dict[str, str | list[str] | bool] = {}
        if data.name is not None:
            update_data["name"] = data.name
        if data.description is not None:
            update_data["description"] = data.description
        if data.scopes is not None:
            update_data["scopes"] = data.scopes
        if data.is_active is not None:
            update_data["is_active"] = data.is_active

        if update_data:
            api_key = await self.apikey_repository.update(key_id, update_data)

            # Log update.
            await self.audit_repository.log_user_action_safe(
                action=AuditAction.APIKEY_UPDATE,
                user_id=user_id,
                tenant_id=tenant_id,
                email=user_email,
                resource_type="api_key",
                resource_id=str(key_id),
                changes=update_data,
            )

        return api_key

    async def revoke(
        self,
        key_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
        user_email: str | None = None,
    ) -> bool:
        """Revoke an API key.

        Args:
            key_id: API key ID
            user_id: User revoking the key
            tenant_id: Tenant context
            user_email: Pre-fetched user email for audit logging (avoids N+1)

        Returns:
            True if revoked
        """
        api_key = await self.apikey_repository.get(key_id)
        if not api_key or api_key.user_id != user_id:
            return False

        await self.apikey_repository.update(key_id, {"is_active": False})

        # Log revocation.
        await self.audit_repository.log_user_action_safe(
            action=AuditAction.APIKEY_REVOKE,
            user_id=user_id,
            tenant_id=tenant_id,
            email=user_email,
            resource_type="api_key",
            resource_id=str(key_id),
        )

        logger.info("api_key_revoked", key_id=str(key_id))

        return True

    async def delete(
        self,
        key_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
        user_email: str | None = None,
    ) -> bool:
        """Delete an API key (soft delete).

        Args:
            key_id: API key ID
            user_id: User deleting the key
            tenant_id: Tenant context
            user_email: Pre-fetched user email for audit logging (avoids N+1)

        Returns:
            True if deleted
        """
        api_key = await self.apikey_repository.get(key_id)
        if not api_key or api_key.user_id != user_id:
            return False

        await self.apikey_repository.delete(key_id)

        # Log deletion.
        await self.audit_repository.log_user_action_safe(
            action=AuditAction.APIKEY_DELETE,
            user_id=user_id,
            tenant_id=tenant_id,
            email=user_email,
            resource_type="api_key",
            resource_id=str(key_id),
        )

        logger.info("api_key_deleted", key_id=str(key_id))

        return True
