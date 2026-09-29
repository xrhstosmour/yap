"""A push kind one account has turned off.

Rows exist only for kinds somebody has changed. Every kind is on until
they say otherwise, which is what "no row" means, so the table stays
empty for the overwhelming majority of accounts and a new kind needs no
backfill to start delivering.

The kind is free text rather than an enum. What an application notifies
about is the application's own vocabulary, and pinning it here would mean
a template migration every time a downstream project invented one.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import UniqueConstraint
from sqlmodel import Field

from app.models.base import BaseModel

MAXIMUM_KIND_LENGTH = 64


class NotificationPreference(BaseModel, table=True):
    """One account's answer for one kind of push.

    Attributes:
        user_id: FK to the account this answer belongs to.
        kind: What the answer is about, in the application's own
            vocabulary. Matched exactly against the kind passed at send
            time.
        is_enabled: Whether to deliver it. A row is written for an
            enabled kind too, so turning one back on is an update rather
            than a delete and the history stays readable.
        created_at: When this answer was first given.
        updated_at: When it was last changed.
        deleted_at: Soft delete timestamp.
        tenant_id: Tenant context.
    """

    __tablename__ = "notification_preferences"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        # One answer per account per kind. Without this a double tap
        # writes two rows and which one wins is whichever the query
        # happens to read first.
        UniqueConstraint(
            "user_id",
            "kind",
            name="uq_notification_preferences_user_id_kind",
        ),
    )

    user_id: UUID = Field(
        nullable=False,
        index=True,
        foreign_key="users.id",
    )
    kind: str = Field(nullable=False, max_length=MAXIMUM_KIND_LENGTH)
    is_enabled: bool = Field(nullable=False, default=True)
