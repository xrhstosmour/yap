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
    # Intentionally does nothing. This revision used to make
    # `ix_files_content_hash` globally unique, to close the upload dedup
    # race. Three revisions later that is replaced by a unique constraint on
    # `(tenant_id, uploaded_by, content_hash)`, which is what the dedup is
    # actually scoped to, and the global index is dropped.
    #
    # Enforcing global uniqueness on the way past therefore blocked upgrades
    # on data the final schema accepts: two tenants holding the same content
    # is legal at head, but aborted here with
    # `could not create unique index "ix_files_content_hash"`, stranding the
    # database three revisions short of head with no way forward.
    #
    # The revision is kept rather than deleted so existing `alembic_version`
    # values stay resolvable. Databases that already ran it are unaffected,
    # the index it created is dropped by `20260812103128195` either way.
    pass


def downgrade() -> None:
    pass
