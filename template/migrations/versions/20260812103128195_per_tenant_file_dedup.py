"""Scope file dedup to `(tenant_id, content_hash)` instead of globally

Only the index drop is applied. The unique constraint this revision
originally added is superseded by the next one and is no longer created
here, see the comment in `upgrade`.

Revision ID: 20260812103128195
Revises: 20260812102141972
Create Date: 2026-08-12T10:31:28.195000+00:00

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260812103128195"
down_revision: Union[str, None] = "20260812102141972"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Dropping the index is real and kept: the final schema has no
    # `ix_files_content_hash`.
    op.drop_index(op.f("ix_files_content_hash"), table_name="files")

    # Creating `uq_files_tenant_id_content_hash` is not. The very next
    # revision replaces it with the triple, and this one is stricter, so it
    # rejected two users in the same tenant uploading the same file, which
    # head allows. Same reasoning as `20260731171240225`.


def downgrade() -> None:
    op.drop_constraint(
        "uq_files_tenant_id_content_hash", "files", type_="unique"
    )
    op.create_index(
        op.f("ix_files_content_hash"), "files", ["content_hash"], unique=True
    )
