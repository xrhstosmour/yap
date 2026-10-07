"""Add the consent records table

Append only. A withdrawal is a new row saying no, never an edit of the row
that said yes, because what has to be answerable later is what was agreed,
when, and to which version of the document. An updated row answers only the
first of those.

That is why there is no unique constraint on `(user_id, kind)`, unlike
`notification_preferences`: more than one row per kind is the point. The
compound index is ordered `(user_id, kind, created_at)` so the only read
there is, the newest answer per kind for one account, is covered.

`kind` is free text for the same reason it is on notification preferences:
what an application needs consent for is its own vocabulary, and pinning it
here would mean a template migration every time a downstream project named
a new one.

Revision ID: 20261007061302248
Revises: 20260929160020618
Create Date: 2026-10-07T06:13:02.260105+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261007061302248"
down_revision: str | None = "20260929160020618"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "consent_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False
        ),
        sa.Column("is_granted", sa.Boolean(), nullable=False),
        sa.Column(
            "document_version",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_consent_records_created_at"),
        "consent_records",
        ["created_at"],
    )
    op.create_index(
        op.f("ix_consent_records_deleted_at"),
        "consent_records",
        ["deleted_at"],
    )
    op.create_index(
        op.f("ix_consent_records_tenant_id"),
        "consent_records",
        ["tenant_id"],
    )
    op.create_index(
        op.f("ix_consent_records_user_id"),
        "consent_records",
        ["user_id"],
    )
    op.create_index(
        "ix_consent_records_user_id_kind_created_at",
        "consent_records",
        ["user_id", "kind", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_consent_records_user_id_kind_created_at",
        table_name="consent_records",
    )
    op.drop_index(
        op.f("ix_consent_records_user_id"), table_name="consent_records"
    )
    op.drop_index(
        op.f("ix_consent_records_tenant_id"), table_name="consent_records"
    )
    op.drop_index(
        op.f("ix_consent_records_deleted_at"), table_name="consent_records"
    )
    op.drop_index(
        op.f("ix_consent_records_created_at"), table_name="consent_records"
    )
    op.drop_table("consent_records")
