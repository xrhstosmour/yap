"""Add unique constraint on `files.content_hash`

Revision ID: 20260731171240225
Revises: 20260731174425314
Create Date: 2026-07-31T17:12:40.225228+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260731171240225"
down_revision: Union[str, None] = "20260731174425314"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Closes the upload dedup race: without this constraint, two concurrent
    # uploads of identical content can both miss each other's in-flight row
    # in the application-level check and insert duplicate `files` rows.
    op.drop_index(op.f("ix_files_content_hash"), table_name="files")
    op.create_index(
        op.f("ix_files_content_hash"), "files", ["content_hash"], unique=True
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_files_content_hash"), table_name="files")
    op.create_index(
        op.f("ix_files_content_hash"), "files", ["content_hash"], unique=False
    )
