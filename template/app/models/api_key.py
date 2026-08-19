"""API Key model for programmatic authentication.

This module defines the APIKey model for machine-to-machine
authentication and third-party integrations.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import JSON
from sqlalchemy import Index
from sqlalchemy.orm import relationship
from sqlmodel import Field
from sqlmodel import Relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.tenant import Tenant
    from app.models.user import User


class APIKey(BaseModel, table=True):
    """API Key model for programmatic access.

    API keys allow machine-to-machine authentication without
    interactive login. Keys can have scopes to limit permissions.

    Attributes:
        id: UUID primary key
        key_id: Public identifier for the key (shown in UI)
        key_hash: Bcrypt hash of the full key (stored securely)
        key_prefix: First 8 chars for identification
        name: Human-readable name for the key
        description: Description of the key's purpose
        scopes: List of permission scopes
        is_active: Whether the key is currently valid
        last_used_at: When the key was last used
        expires_at: When the key expires (null for no expiry)
        tenant_id: Organization that owns this key
        user_id: User who created this key
        created_at: When the key was created
        updated_at: When the key was last modified
        deleted_at: Soft delete timestamp

    Security:
        The full API key is only shown once at creation.
        Only the hash is stored for verification.
        Keys can be revoked at any time by setting is_active=False.
    """

    __tablename__ = "api_keys"  # pyright: ignore[reportAssignmentType]
    # Compound indexes for common query patterns.
    # list_by_user() filters on (user_id, is_active), compound covers both.
    # deactivate_expired_keys() filters on (expires_at, is_active), same.
    __table_args__ = (
        Index("ix_api_keys_user_id_is_active", "user_id", "is_active"),
        Index("ix_api_keys_expires_at_is_active", "expires_at", "is_active"),
    )

    key_id: str = Field(
        unique=True,
        index=True,
        nullable=False,
        max_length=100,
    )

    key_hash: str = Field(
        nullable=False,
        max_length=255,
    )

    key_prefix: str = Field(
        nullable=False,
        max_length=20,
    )

    name: str = Field(
        nullable=False,
        max_length=255,
    )

    description: str | None = Field(
        default=None,
        max_length=500,
    )

    scopes: list[str] = Field(
        default_factory=list,
        sa_type=JSON,
        nullable=False,
    )

    is_active: bool = Field(default=True, nullable=False)

    last_used_at: datetime | None = Field(
        default=None,
        nullable=True,
    )

    expires_at: datetime | None = Field(
        default=None,
        nullable=True,
    )

    # Multi-tenancy.
    tenant_id: UUID = Field(  # pyright: ignore[reportAssignmentType]
        nullable=False,
        index=True,
        foreign_key="tenants.id",
    )

    user_id: UUID = Field(
        nullable=False,
        index=True,
        foreign_key="users.id",
    )

    # Relationships.
    tenant: Tenant = Relationship(
        sa_relationship=relationship(
            "Tenant",
            back_populates="api_keys",
            lazy="selectin",
        ),
    )

    user: User = Relationship(
        sa_relationship=relationship(
            "User",
            back_populates="api_keys",
            lazy="selectin",
        ),
    )

    def is_expired(self) -> bool:
        """Check if the API key has expired.

        Returns:
            True if expires_at is set and is in the past
        """
        if self.expires_at is None:
            return False
        return datetime.now(UTC) > self.expires_at

    def is_valid(self) -> bool:
        """Check if the API key is valid for use.

        Returns:
            True if active, not expired, and not deleted
        """
        return self.is_active and not self.is_expired() and self.deleted_at is None
