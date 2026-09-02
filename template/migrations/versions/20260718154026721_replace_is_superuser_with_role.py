"""Replace `is_superuser` boolean with `UserRole` enum

Revision ID: 20260718154026721
Revises: a7b8c9d0e1f2
Create Date: 2026-07-18 15:40:26.721000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260718154026721"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE TYPE userrole AS ENUM ('superuser', 'user')")
    # The type is created by the statement above. `op.add_column` emits only
    # the `ALTER TABLE`, never a `CREATE TYPE`, so nothing has to suppress
    # one. `create_type=False` belongs to `postgresql.ENUM`, and `sa.Enum`
    # swallowed it without setting an attribute or a dialect option.
    op.add_column(
        "users",
        sa.Column("role", sa.Enum("superuser", "user", name="userrole"), nullable=True),
    )
    op.execute("UPDATE users SET role = 'superuser' WHERE is_superuser = TRUE")
    op.execute("UPDATE users SET role = 'user' WHERE role IS NULL")
    op.alter_column("users", "role", nullable=False, server_default="user")
    op.drop_column("users", "is_superuser")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_superuser", sa.Boolean(), nullable=True),
    )
    op.execute("UPDATE users SET is_superuser = TRUE WHERE role = 'superuser'")
    op.execute("UPDATE users SET is_superuser = FALSE WHERE is_superuser IS NULL")
    op.alter_column("users", "is_superuser", nullable=False)
    op.drop_column("users", "role")
    op.execute("DROP TYPE userrole")
