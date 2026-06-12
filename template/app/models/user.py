"""User model for authentication and authorization.

This module defines the User model with support for password-based
authentication and role-based access control.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import EmailStr
from sqlalchemy.orm import relationship
from sqlmodel import Field
from sqlmodel import Relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.api_key import APIKey
    from app.models.oauth_account import OAuthAccount
    from app.models.tenant import Tenant


class User(BaseModel, table=True):
    """User model for authentication.

    Represents a user in the system with password-based authentication.
    Users belong to a tenant (organization) and can have API keys and
    linked OAuth accounts.

    Attributes:
        id: UUID primary key
        email: Unique email address
        full_name: User's display name
        hashed_password: Bcrypt hash of the password
        is_active: Whether the user can log in
        is_superuser: Full admin access (bypasses tenant restrictions)
        is_verified: Whether email has been verified
        is_2fa_enabled: Whether TOTP 2FA is enabled
        totp_secret_encrypted: Encrypted TOTP secret for 2FA enrollment/login
        totp_confirmed_at: Timestamp when TOTP enrollment was confirmed
        tenant_id: Organization this user belongs to
        created_at: Account creation timestamp
        updated_at: Last modification timestamp
        deleted_at: Soft delete timestamp

    Relationships:
        tenant: The organization this user belongs to
        api_keys: API keys owned by this user
        oauth_accounts: Linked OAuth provider accounts

    Note:
        Superusers (is_superuser=True) have access to all tenants
        but still belong to a primary tenant.
    """

    __tablename__ = "users"  # pyright: ignore[reportAssignmentType]

    email: EmailStr = Field(
        unique=True,
        index=True,
        nullable=False,
        max_length=255,
    )

    full_name: str | None = Field(
        default=None,
        max_length=255,
    )

    hashed_password: str = Field(
        nullable=False,
        max_length=255,
    )

    is_active: bool = Field(default=True, nullable=False)

    is_superuser: bool = Field(default=False, nullable=False)

    is_verified: bool = Field(default=False, nullable=False)

    token_version: int = Field(default=1, nullable=False)

    is_2fa_enabled: bool = Field(default=False, nullable=False)

    totp_secret_encrypted: str | None = Field(
        default=None,
        nullable=True,
        max_length=500,
    )

    totp_confirmed_at: datetime | None = Field(
        default=None,
        nullable=True,
    )

    # Multi-tenancy.
    tenant_id: UUID | None = Field(
        default=None,
        nullable=True,
        index=True,
        foreign_key="tenants.id",
    )

    # Relationships.
    tenant: Tenant = Relationship(
        sa_relationship=relationship(
            "Tenant",
            back_populates="users",
            lazy="selectin",
        ),
    )

    api_keys: list[APIKey] = Relationship(
        sa_relationship=relationship(
            "APIKey",
            back_populates="user",
            lazy="selectin",
        ),
    )

    oauth_accounts: list[OAuthAccount] = Relationship(
        sa_relationship=relationship(
            "OAuthAccount",
            back_populates="user",
            lazy="selectin",
        ),
    )
