"""WebAuthn credential model for passkey (FIDO2) authentication.

Stores public key credentials registered via the WebAuthn API.
Each row represents one passkey (device-bound or cross-platform).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlmodel import Field

from app.models.base import BaseModel


class WebAuthnCredential(BaseModel, table=True):
    """WebAuthn public key credential for passkey authentication.

    Attributes:
        user_id: FK to the owning user.
        credential_id: Base64url-encoded raw credential ID from the authenticator.
        public_key: PEM-encoded EC2 or RSA public key.
        user_handle: Base64url-encoded user handle (user.id) used during registration.
        sign_count: Signature counter value for replay detection.
        device_name: Human-readable name set by the user
            (e.g. "iPhone 15", "YubiKey 5").
        last_used_at: When this credential was last used for authentication.
        created_at: When this credential was registered.
        updated_at: When this credential was last modified.
        deleted_at: Soft delete timestamp.
        tenant_id: Tenant context.
    """

    __tablename__ = "webauthn_credentials"  # pyright: ignore[reportAssignmentType]

    user_id: UUID = Field(
        nullable=False,
        index=True,
        foreign_key="users.id",
    )

    credential_id: str = Field(
        nullable=False,
        unique=True,
        index=True,
        max_length=500,
    )

    public_key: str = Field(
        nullable=False,
        max_length=1000,
    )

    user_handle: str = Field(
        nullable=False,
        max_length=100,
    )

    sign_count: int = Field(
        default=0,
        nullable=False,
    )

    device_name: str = Field(
        nullable=False,
        max_length=255,
    )

    last_used_at: datetime | None = Field(
        default=None,
        nullable=True,
    )
