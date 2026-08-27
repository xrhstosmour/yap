"""User model for authentication and authorization.

This module defines the User model with support for password-based
authentication and role-based access control.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID
from uuid import uuid4

from pydantic import EmailStr
from sqlalchemy import Enum as SAEnum
from sqlalchemy import event
from sqlalchemy.orm import relationship
from sqlmodel import Field
from sqlmodel import Relationship

from app.core.encryption import EncryptedString
from app.core.encryption import crypto
from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.api_key import APIKey
    from app.models.oauth_account import OAuthAccount
    from app.models.tenant import Tenant


class UserRole(enum.StrEnum):
    """Enumeration of user roles for role-based access control."""

    SUPERUSER = "superuser"
    USER = "user"


class User(BaseModel, table=True):
    """User model for authentication.

    Represents a user in the system with password-based authentication.
    Users belong to a tenant (organization) and can have API keys and
    linked OAuth accounts.

    Attributes:
        id: UUID primary key
        email: Unique email address. Encrypted at rest (`EncryptedString`);
            reads/writes are transparent plain text. `email_hash` is a
            deterministic HMAC of the address, used for equality lookups
            (e.g. login) and to enforce uniqueness, since ciphertext is
            randomised and cannot be compared or indexed directly.
        email_hash: Deterministic HMAC-SHA256 hash of `email`, unique and
            indexed. See `CryptoService.hash_for_search()`. Exception: when
            `email` is cleared to `""` (GDPR erasure), a random token is
            salted in instead, so repeated erasures don't collide on this
            unique column, see `_sync_email_hash`.
        full_name: User's display name. Left unencrypted, see note below.
        phone: Phone number in E.164 format. Encrypted at rest, same
            pattern as `email`. `phone_hash` enables exact-match lookups.
        phone_hash: Deterministic HMAC-SHA256 hash of `phone`, indexed.
        hashed_password: Bcrypt hash of the password
        is_active: Whether the user can log in
        role: User role for access control (superuser, user)
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
        `full_name` is intentionally NOT encrypted. It backs the
        greeklish/trigram fuzzy search in `UserRepository.search()`
        (`app/core/search.py`), which requires substring/similarity
        matching directly in SQL. Fernet ciphertext is randomised per
        value, so encrypted text cannot support trigram or full-text
        search, only exact-match lookups via a deterministic HMAC hash
        (as used for `email`/`phone`) are possible on encrypted columns.
        Encrypting `full_name` would require dropping name search
        entirely or building a bespoke searchable-encryption scheme
        (e.g. per-token HMAC blind indexing), which is out of scope here.
    """

    __tablename__ = "users"  # pyright: ignore[reportAssignmentType]

    email: EmailStr = Field(
        nullable=False,
        max_length=255,
        sa_type=EncryptedString(512),  # type: ignore[call-overload]
    )

    email_hash: str = Field(
        unique=True,
        index=True,
        nullable=False,
        max_length=64,
    )

    full_name: str | None = Field(
        default=None,
        max_length=255,
    )

    phone: str | None = Field(
        default=None,
        max_length=16,
        nullable=True,
        sa_type=EncryptedString(255),  # type: ignore[call-overload]
    )

    phone_hash: str | None = Field(
        default=None,
        index=True,
        nullable=True,
        max_length=64,
    )

    hashed_password: str = Field(
        nullable=False,
        max_length=255,
    )

    is_active: bool = Field(default=True, nullable=False)

    role: UserRole = Field(
        default=UserRole.USER,
        nullable=False,
        sa_type=SAEnum(
            UserRole,
            values_callable=lambda e: [m.value for m in e],
        ),  # type: ignore[call-overload]
    )

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

    # Collections are `raise`, not `selectin`: eager-loading them made every
    # `User` fetch drag in unbounded child tables nothing reads. Ask for one
    # explicitly where it is genuinely needed:
    #
    #     select(User).options(selectinload(User.api_keys))
    api_keys: list[APIKey] = Relationship(
        sa_relationship=relationship(
            "APIKey",
            back_populates="user",
            lazy="raise",
        ),
    )

    oauth_accounts: list[OAuthAccount] = Relationship(
        sa_relationship=relationship(
            "OAuthAccount",
            back_populates="user",
            lazy="raise",
        ),
    )


@event.listens_for(User.email, "set")
def _sync_email_hash(
    target: User, value: str | None, oldvalue: object, initiator: object
) -> None:
    """Keep `email_hash` in sync whenever `email` is assigned.

    Fires on every attribute assignment, including model construction
    (`User(email=...)`) and `setattr()`, so callers never compute the
    search hash themselves. Does not fire on ORM load, the hash is
    read back as its own column value there.

    `email_hash` is `nullable=False`, unlike `phone_hash`, so an empty
    string is hashed rather than skipped: `email_hash` must always reflect
    the current `email`, including a caller clearing it to `""` (e.g. a
    GDPR erasure flow). Skipping that assignment would leave `email_hash`
    pointing at the erased value's hash, silently defeating the erasure.
    Only `None` is skipped, since `hash_for_search` takes a `str` and
    `email` is only ever `None` transiently, before pydantic validation
    has run.

    The empty string is handled separately from a real address: `email_hash`
    is `unique=True`, and `hash_for_search("")` is deterministic, so every
    erased user would otherwise get the exact same hash and only the first
    erasure in the database's lifetime would succeed, every later one would
    hit the unique constraint. A fresh random token is salted in instead,
    so each erasure still produces a hash that does not trace back to the
    real email, but no longer collides with other erased users.
    """
    if value is not None:
        target.email_hash = (
            crypto.hash_for_search(value)
            if value
            else crypto.hash_for_search(f"erased:{uuid4()}")
        )


@event.listens_for(User.phone, "set")
def _sync_phone_hash(
    target: User, value: str | None, oldvalue: object, initiator: object
) -> None:
    """Keep `phone_hash` in sync whenever `phone` is assigned. See `_sync_email_hash`."""
    target.phone_hash = crypto.hash_for_search(value) if value else None
