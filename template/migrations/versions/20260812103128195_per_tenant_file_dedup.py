"""Scope file dedup to `(tenant_id, content_hash)` instead of globally

Existing data trivially satisfies the new constraint: content_hash was
already globally unique before this migration, so no two rows can share
a content_hash regardless of tenant_id, and no backfill is needed.

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
    op.drop_index(op.f("ix_files_content_hash"), table_name="files")
    op.create_unique_constraint(
        "uq_files_tenant_id_content_hash",
        "files",
        ["tenant_id", "content_hash"],
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_files_tenant_id_content_hash", "files", type_="unique"
    )
    op.create_index(
        op.f("ix_files_content_hash"), "files", ["content_hash"], unique=True
    )
