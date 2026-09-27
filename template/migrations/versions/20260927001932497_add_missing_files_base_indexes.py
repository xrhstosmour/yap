"""Add the missing base model indexes on the `files` table

`BaseModel` declares `index=True` on `created_at`, `deleted_at` and
`tenant_id`, and every other table got those indexes when it was created.
The `files` table was written by hand and only indexed `content_hash` and
`uploaded_by`, so it has been missing three indexes the model says it has.

Without them, `_apply_tenant_filter` and `_apply_soft_delete_filter`, which
are on the path of every single file query, have no index to use, and
`alembic --autogenerate` proposes recreating them on every run.

Revision ID: 20260927001932497
Revises: 20260908103304918
Create Date: 2026-09-27T00:19:32.510509+00:00

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260927001932497"
down_revision: Union[str, None] = "20260908103304918"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(op.f("ix_files_created_at"), "files", ["created_at"], unique=False)
    op.create_index(op.f("ix_files_deleted_at"), "files", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_files_tenant_id"), "files", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_files_tenant_id"), table_name="files")
    op.drop_index(op.f("ix_files_deleted_at"), table_name="files")
    op.drop_index(op.f("ix_files_created_at"), table_name="files")
