"""Scope file dedup to `(tenant_id, uploaded_by, content_hash)`

Narrows the constraint added by `20260812103128195` one level further,
for the same reason it narrowed the global one: a row carries a single
`uploaded_by`, so sharing it across owners hands the second uploader the
first one's filename, visibility and file ID, and a reference they can
never release.

This is the constraint the final schema actually carries. The two
stricter ones on the way here are no longer enforced, so a database
whose data is legal at head reaches head instead of aborting partway.
Data that genuinely violates this triple still fails, here, which is
the correct place for it to fail.

Revision ID: 20260828120515761
Revises: 20260812103128195
Create Date: 2026-08-28T12:05:15.761150+00:00

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260828120515761"
down_revision: Union[str, None] = "20260812103128195"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `IF EXISTS` because `20260812103128195` no longer creates it. Databases
    # migrated before that change still carry it and still need it dropped.
    op.execute(
        "ALTER TABLE files DROP CONSTRAINT IF EXISTS uq_files_tenant_id_content_hash"
    )
    op.create_unique_constraint(
        "uq_files_tenant_id_uploaded_by_content_hash",
        "files",
        ["tenant_id", "uploaded_by", "content_hash"],
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_files_tenant_id_uploaded_by_content_hash", "files", type_="unique"
    )
    op.create_unique_constraint(
        "uq_files_tenant_id_content_hash",
        "files",
        ["tenant_id", "content_hash"],
        postgresql_nulls_not_distinct=True,
    )
