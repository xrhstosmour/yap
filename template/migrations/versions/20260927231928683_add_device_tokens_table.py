"""Add the device tokens table

`token` is unique across the whole table rather than per user, because it
addresses one install of the app and an install is signed into one
account at a time. Registering a token another account holds moves it,
which is what a handed-over phone needs, and the constraint is what makes
that a move rather than a silent second delivery.

The index on `(user_id, deleted_at)` covers the only query that matters at
send time: every live token belonging to a page of recipients.

The downgrade drops the `deviceplatform` type as well as the table.
`create_table` creates the type implicitly, so leaving it behind would
make the next upgrade fail with "type already exists" rather than run.

Revision ID: 20260927231928683
Revises: 20260927065302816
Create Date: 2026-09-27T23:19:28.683847+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260927231928683"
down_revision: str | None = "20260927065302816"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "token", sqlmodel.sql.sqltypes.AutoString(length=512), nullable=False
        ),
        sa.Column(
            "platform",
            sa.Enum("IOS", "ANDROID", name="deviceplatform"),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_device_tokens_created_at"), "device_tokens", ["created_at"]
    )
    op.create_index(
        op.f("ix_device_tokens_deleted_at"), "device_tokens", ["deleted_at"]
    )
    op.create_index(op.f("ix_device_tokens_tenant_id"), "device_tokens", ["tenant_id"])
    op.create_index(op.f("ix_device_tokens_user_id"), "device_tokens", ["user_id"])
    op.create_index(
        op.f("ix_device_tokens_token"), "device_tokens", ["token"], unique=True
    )
    op.create_index(
        "ix_device_tokens_user_id_deleted_at",
        "device_tokens",
        ["user_id", "deleted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_device_tokens_user_id_deleted_at", table_name="device_tokens")
    op.drop_index(op.f("ix_device_tokens_token"), table_name="device_tokens")
    op.drop_index(op.f("ix_device_tokens_user_id"), table_name="device_tokens")
    op.drop_index(op.f("ix_device_tokens_tenant_id"), table_name="device_tokens")
    op.drop_index(op.f("ix_device_tokens_deleted_at"), table_name="device_tokens")
    op.drop_index(op.f("ix_device_tokens_created_at"), table_name="device_tokens")
    op.drop_table("device_tokens")
    sa.Enum(name="deviceplatform").drop(op.get_bind(), checkfirst=True)
