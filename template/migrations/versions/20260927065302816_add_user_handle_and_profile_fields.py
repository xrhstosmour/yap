"""Add the user handle and profile fields

`username` is unique across the whole table rather than per tenant,
because a handle is what one person gives another to find them and it
has to resolve the same way everywhere. `username_normalized` carries
the accent-folded lowercase form, so the same handle cannot be claimed
twice in two spellings, and it is trigram indexed so a partial or
misspelt handle still matches.

`avatar_file_id` is deliberately not a foreign key: `files.uploaded_by`
already points at `users`, and a constraint back the other way makes the
two tables mutually dependent.

Revision ID: 20260927065302816
Revises: 20260927001932497
Create Date: 2026-09-27T06:53:02.816000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260927065302816"
down_revision: str | None = "20260927001932497"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "username", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "username_normalized",
            sqlmodel.sql.sqltypes.AutoString(length=32),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column("bio", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
    )
    op.add_column("users", sa.Column("avatar_file_id", sa.Uuid(), nullable=True))
    op.create_unique_constraint("uq_users_username", "users", ["username"])
    op.create_index(
        op.f("ix_users_username_normalized"),
        "users",
        ["username_normalized"],
        unique=False,
    )
    op.create_index(
        "ix_users_username_normalized_trigram",
        "users",
        ["username_normalized"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"username_normalized": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_users_username_normalized_trigram", table_name="users")
    op.drop_index(op.f("ix_users_username_normalized"), table_name="users")
    op.drop_constraint("uq_users_username", "users", type_="unique")
    op.drop_column("users", "avatar_file_id")
    op.drop_column("users", "bio")
    op.drop_column("users", "username_normalized")
    op.drop_column("users", "username")
