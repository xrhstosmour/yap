"""Add GIN trigram indexes on tenants.name and tenants.slug.

`TenantRepository.list_tenants` searches `tenants.name` and `tenants.slug`
with a leading-wildcard `ILIKE(f"%{search}%")`, which the existing plain
B-tree index (`ix_tenants_name`) cannot serve, forcing a sequential scan.

Unlike the `users.email`/`users.full_name` trigram indexes added in
`e3c4b5a6d7f8` (which wrap the column in `immutable_unaccent()` to match
the `unaccent()`-aware search helpers in `app/core/search.py`),
`list_tenants` filters on the raw columns with a plain `.ilike()`, so the
indexes here are built directly on `name`/`slug` without the unaccent
wrapper, that is what the planner will actually match against this
query's `ILIKE` expressions.

Revision ID: 7089da61aec9
Revises: 20260718154026721
Create Date: 2026-07-31 16:00:00.000000

"""

from typing import Sequence
from typing import Union

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "7089da61aec9"
down_revision: Union[str, None] = "20260718154026721"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # GIN trigram index on tenants.name (required for trigram ILIKE / %).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tenants_name_trgm "
        "ON tenants USING gin (name gin_trgm_ops)"
    )

    # GIN trigram index on tenants.slug (required for trigram ILIKE / %).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tenants_slug_trgm "
        "ON tenants USING gin (slug gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_tenants_slug_trgm")
    op.execute("DROP INDEX IF EXISTS ix_tenants_name_trgm")
