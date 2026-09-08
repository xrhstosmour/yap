"""Drop dead `files.is_public` column

Nothing in the application ever reads `is_public` to gate access; it was
only ever written and echoed back verbatim in the upload/metadata
responses. Removing it, and every reference to it, rather than leaving a
field that looks like an access control check but isn't one.

Revision ID: 20260908103304918
Revises: 20260828120515761
Create Date: 2026-09-08T10:33:04.918000+00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260908103304918"
down_revision: Union[str, None] = "20260828120515761"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("files", "is_public")


def downgrade() -> None:
    op.add_column(
        "files",
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
