"""TOTP recovery code model for 2FA backup authentication.

This module defines the TotpRecoveryCode model used to store
bcrypt-hashed one-time recovery codes for 2FA-enabled users.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID
from uuid import uuid7

from sqlmodel import Field
from sqlmodel import SQLModel

from app.models.base import get_utc_now


class TotpRecoveryCode(SQLModel, table=True):
    """Single-use backup code for 2FA recovery.

    Stores a bcrypt hash of a one-time recovery code. Each user
    receives 10 codes on 2FA enrollment. Codes are consumed on use
    and cannot be reused.

    Attributes:
        id: UUIDv7 primary key.
        user_id: FK to the owning user.
        code_hash: Bcrypt hash of the plaintext recovery code.
        created_at: When the code was generated.
        used_at: When the code was consumed (null = unused).
    """

    __tablename__ = "totp_recovery_codes"  # pyright: ignore[reportAssignmentType]

    id: UUID = Field(
        default_factory=uuid7,
        primary_key=True,
        nullable=False,
    )

    user_id: UUID = Field(
        nullable=False,
        index=True,
        foreign_key="users.id",
    )

    code_hash: str = Field(
        nullable=False,
        max_length=255,
    )

    created_at: datetime = Field(
        default_factory=get_utc_now,
        nullable=False,
    )

    used_at: datetime | None = Field(
        default=None,
        nullable=True,
    )
