"""Add `is_tenant_owner` to `users`

Tenant billing management (checkout, cancel, payment methods) requires a
dedicated per-user flag rather than the global `UserRole` enum, since that
enum is superuser/user and unrelated to tenant billing authority.

Revision ID: 20260806115848737
Revises: 20260731171240225
Create Date: 2026-08-06 11:58:48.737000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260806115848737"
down_revision: Union[str, None] = "20260731171240225"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_tenant_owner",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column("users", "is_tenant_owner", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "is_tenant_owner")
