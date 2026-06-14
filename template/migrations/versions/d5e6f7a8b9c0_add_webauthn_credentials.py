"""Create webauthn_credentials table for passkey authentication

Revision ID: d5e6f7a8b9c0
Revises: e3c4b5a6d7f8
Create Date: 2026-06-13 15:00:00.000000

"""

from typing import Sequence
from typing import Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "e3c4b5a6d7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "webauthn_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "credential_id",
            sqlmodel.sql.sqltypes.AutoString(length=500),
            nullable=False,
        ),
        sa.Column(
            "public_key",
            sqlmodel.sql.sqltypes.AutoString(length=1000),
            nullable=False,
        ),
        sa.Column(
            "user_handle",
            sqlmodel.sql.sqltypes.AutoString(length=100),
            nullable=False,
        ),
        sa.Column("sign_count", sa.Integer(), nullable=False),
        sa.Column(
            "device_name",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_id", name="uq_webauthn_credentials_id"),
    )
    op.create_index(
        op.f("ix_webauthn_credentials_user_id"),
        "webauthn_credentials",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_webauthn_credentials_user_id"),
        table_name="webauthn_credentials",
    )
    op.drop_table("webauthn_credentials")
