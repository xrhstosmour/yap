"""Add the notification preferences table

Rows exist only for kinds somebody has answered. Absent means on, so the
table starts empty, stays empty for accounts that never change anything,
and a newly invented kind delivers without a backfill.

`kind` is free text rather than an enum. What an application notifies
about is its own vocabulary, and pinning it in the template would mean a
migration here every time a downstream project named a new one. That also
keeps the downgrade simple: no type to drop, only the table.

The unique constraint on `(user_id, kind)` is what makes a repeated tap
an update rather than a second row nobody can order.

Revision ID: 20260929160020618
Revises: 20260927231928683
Create Date: 2026-09-29T16:00:20.629008+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260929160020618"
down_revision: str | None = "20260927231928683"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False
        ),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "kind", name="uq_notification_preferences_user_id_kind"
        ),
    )
    op.create_index(
        op.f("ix_notification_preferences_created_at"),
        "notification_preferences",
        ["created_at"],
    )
    op.create_index(
        op.f("ix_notification_preferences_deleted_at"),
        "notification_preferences",
        ["deleted_at"],
    )
    op.create_index(
        op.f("ix_notification_preferences_tenant_id"),
        "notification_preferences",
        ["tenant_id"],
    )
    op.create_index(
        op.f("ix_notification_preferences_user_id"),
        "notification_preferences",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_notification_preferences_user_id"),
        table_name="notification_preferences",
    )
    op.drop_index(
        op.f("ix_notification_preferences_tenant_id"),
        table_name="notification_preferences",
    )
    op.drop_index(
        op.f("ix_notification_preferences_deleted_at"),
        table_name="notification_preferences",
    )
    op.drop_index(
        op.f("ix_notification_preferences_created_at"),
        table_name="notification_preferences",
    )
    op.drop_table("notification_preferences")
