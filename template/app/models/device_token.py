"""A device registered to receive push notifications.

One row per install, not per user: the same person on a phone and a tablet
is two rows, and the same phone signed into a second account is two more.
The token is what the push service addresses, so it is unique across the
table rather than per user. A token that moves between accounts, which is
what happens when a phone is handed over or an account is switched, is
reassigned rather than duplicated, so the previous owner stops receiving
notifications on a device that is no longer theirs.

Tokens expire on their own schedule and without telling us. The push
service reports an unregistered token at send time, and that is the only
reliable signal there is, so rows are pruned on that report rather than by
age.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import Index
from sqlmodel import Field

from app.models.base import BaseModel


class DevicePlatform(StrEnum):
    """Which store the install came from.

    Recorded even when one transport serves both, because a payload that
    renders correctly on one platform can be silently dropped by the
    other, and knowing which installs are which is what makes that
    debuggable.
    """

    IOS = "ios"
    ANDROID = "android"


class DeviceToken(BaseModel, table=True):
    """A push token belonging to one install of the app.

    Attributes:
        user_id: FK to the account this install is signed into.
        token: The push service's address for this install.
        platform: Which store the install came from.
        last_seen_at: When the app last confirmed this token is current.
            Refreshed on every registration, which the client repeats on
            launch, so a stale row is one whose app has not run in a while.
        created_at: When this token was first registered.
        updated_at: When this row was last modified.
        deleted_at: Soft delete timestamp.
        tenant_id: Tenant context.
    """

    __tablename__ = "device_tokens"  # pyright: ignore[reportAssignmentType]
    # Covers the only query that runs at send time: every live token
    # belonging to a page of recipients.
    __table_args__ = (
        Index("ix_device_tokens_user_id_deleted_at", "user_id", "deleted_at"),
    )

    user_id: UUID = Field(
        nullable=False,
        index=True,
        foreign_key="users.id",
    )

    # Unique across the table rather than per user. FCM registration
    # tokens run to roughly 160 characters and APNs device tokens to 64,
    # but neither is documented as bounded, so this leaves room.
    token: str = Field(
        nullable=False,
        unique=True,
        index=True,
        max_length=512,
    )

    platform: DevicePlatform = Field(
        nullable=False,
        max_length=20,
    )

    last_seen_at: datetime | None = Field(
        default=None,
        nullable=True,
    )
