"""Add oauth_accounts table for multi-provider social login

Revision ID: a1b2c3d4e5f6
Revises: 5bdd94fa284b
Create Date: 2026-06-04 12:00:00.000000

"""

from typing import Sequence
from typing import Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "5bdd94fa284b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "oauth_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "provider",
            sqlmodel.sql.sqltypes.AutoString(length=50),
            nullable=False,
        ),
        sa.Column(
            "provider_user_id",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column(
            "provider_email",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "provider_user_id",
            name="uq_oauth_accounts_provider_user_id",
        ),
        # One linked account per user per provider.
        sa.UniqueConstraint(
            "user_id",
            "provider",
            name="uq_oauth_accounts_user_id_provider",
        ),
    )
    op.create_index(
        op.f("ix_oauth_accounts_id"), "oauth_accounts", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_oauth_accounts_created_at"),
        "oauth_accounts",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_oauth_accounts_deleted_at"),
        "oauth_accounts",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_oauth_accounts_tenant_id"),
        "oauth_accounts",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_oauth_accounts_user_id"),
        "oauth_accounts",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_oauth_accounts_provider"),
        "oauth_accounts",
        ["provider"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_oauth_accounts_provider"), table_name="oauth_accounts"
    )
    op.drop_index(
        op.f("ix_oauth_accounts_user_id"), table_name="oauth_accounts"
    )
    op.drop_index(
        op.f("ix_oauth_accounts_tenant_id"), table_name="oauth_accounts"
    )
    op.drop_index(
        op.f("ix_oauth_accounts_deleted_at"), table_name="oauth_accounts"
    )
    op.drop_index(
        op.f("ix_oauth_accounts_created_at"), table_name="oauth_accounts"
    )
    op.drop_index(op.f("ix_oauth_accounts_id"), table_name="oauth_accounts")
    op.drop_table("oauth_accounts")
