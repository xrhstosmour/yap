"""Add phone number field to User model

Revision ID: a7b8c9d0e1f2
Revises: b7c8d9e0f1a2
Create Date: 2026-07-16 14:00:00.000000

"""

from typing import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("phone", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "phone")
