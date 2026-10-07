"""An answer somebody gave to a consent question, kept as history.

Append only. A withdrawal is a new row saying no, never an edit of the row
that said yes, because what has to be answerable later is "what was agreed,
when, and to which version of the document", and an updated row can only
answer the first of those. The current answer is the newest row for a kind.

The kind is free text, for the same reason
:mod:`app.models.notification_preference` gives: what an application needs
consent for is the application's own vocabulary, and pinning it here would
mean a template migration every time a downstream project added one.

Nothing here decides whether an absent answer blocks anything. That is the
application's rule, and it differs per kind: terms usually gate sign up,
marketing never gates anything.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Index
from sqlmodel import Field

from app.models.base import BaseModel

MAXIMUM_KIND_LENGTH = 64
MAXIMUM_VERSION_LENGTH = 64


class ConsentRecord(BaseModel, table=True):
    """One answer, at one moment, to one consent question.

    Attributes:
        user_id: FK to the account that answered.
        kind: What was asked, in the application's own vocabulary, for
            example ``terms`` or ``marketing_email``.
        is_granted: What they said. False is a real answer and is stored as
            one, so a withdrawal is recorded rather than inferred from a
            missing row.
        document_version: Which version of the text they agreed to, where
            there is one. Null for a question with no document behind it.
            Without this, a changed privacy policy silently inherits the
            consent given to the old one.
        created_at: When the answer was given. This is the record.
        updated_at: Set by the base model and not meaningful here, since a
            row is never edited.
        deleted_at: Soft delete timestamp.
        tenant_id: Tenant context.
    """

    __tablename__ = "consent_records"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        # The only read there is: the newest answer per kind for one
        # account. No unique constraint, deliberately, because more than
        # one row per kind is the point.
        Index(
            "ix_consent_records_user_id_kind_created_at",
            "user_id",
            "kind",
            "created_at",
        ),
    )

    user_id: UUID = Field(
        nullable=False,
        index=True,
        foreign_key="users.id",
    )
    kind: str = Field(nullable=False, max_length=MAXIMUM_KIND_LENGTH)
    is_granted: bool = Field(nullable=False)
    document_version: str | None = Field(
        default=None,
        nullable=True,
        max_length=MAXIMUM_VERSION_LENGTH,
    )
