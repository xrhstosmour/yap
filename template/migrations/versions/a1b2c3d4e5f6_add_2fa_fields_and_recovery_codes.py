"""Add 2FA fields to users and create totp_recovery_codes table.

Revision ID: d2f4a6b8c0e1
Revises: c9d1e3f5a7b2
Create Date: 2026-06-05 00:00:00.000000

"""

from typing import Sequence
from typing import Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlmodel.sql.sqltypes import AutoString

# Revision identifiers, used by Alembic.
revision: str = "d2f4a6b8c0e1"
down_revision: Union[str, None] = "c9d1e3f5a7b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add 2FA columns to users table.
    op.add_column(
        "users",
        sa.Column(
            "is_2fa_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "totp_secret_encrypted",
            AutoString(length=500),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column("totp_confirmed_at", sa.DateTime(), nullable=True),
    )

    # Create totp_recovery_codes table.
    op.create_table(
        "totp_recovery_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "code_hash",
            AutoString(length=255),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_totp_recovery_codes_id"),
        "totp_recovery_codes",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_totp_recovery_codes_user_id"),
        "totp_recovery_codes",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_totp_recovery_codes_user_id"), table_name="totp_recovery_codes"
    )
    op.drop_index(op.f("ix_totp_recovery_codes_id"), table_name="totp_recovery_codes")
    op.drop_table("totp_recovery_codes")
    op.drop_column("users", "totp_confirmed_at")
    op.drop_column("users", "totp_secret_encrypted")
    op.drop_column("users", "is_2fa_enabled")
