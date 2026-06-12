"""OAuth account model for multi-provider social login.

Stores one row per (user, provider) pair. Allows a single
user to link multiple OAuth providers (Google, Apple, etc.)
without coupling provider-specific IDs to the User model.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import relationship
from sqlmodel import Field
from sqlmodel import Relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.user import User


class OAuthProvider(StrEnum):
    """Supported OAuth 2.0 / OIDC providers."""

    GOOGLE = "google"
    APPLE = "apple"


class OAuthAccount(BaseModel, table=True):
    """Linked OAuth account for a user.

    One row per (user, provider) pair. A user may link multiple
    providers, but each (provider, provider_user_id) pair maps to
    exactly one user.

    Attributes:
        user_id: FK to the owning user.
        provider: OAuth provider name (e.g. "google", "apple").
        provider_user_id: Subject/sub identifier from the provider.
        provider_email: Email address returned by the provider.
    """

    __tablename__ = "oauth_accounts"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_user_id",
            name="uq_oauth_accounts_provider_user_id",
        ),
        # Enforce one linked account per user per provider.
        UniqueConstraint(
            "user_id",
            "provider",
            name="uq_oauth_accounts_user_id_provider",
        ),
    )

    user_id: UUID = Field(
        nullable=False,
        index=True,
        foreign_key="users.id",
    )

    # Plain str so adding new providers never requires a migration.
    provider: str = Field(
        nullable=False,
        max_length=50,
        index=True,
    )

    provider_user_id: str = Field(
        nullable=False,
        max_length=255,
    )

    provider_email: str | None = Field(
        default=None,
        max_length=255,
    )

    # Relationship back to user.
    user: User = Relationship(
        sa_relationship=relationship(
            "User",
            back_populates="oauth_accounts",
            lazy="selectin",
        ),
    )
