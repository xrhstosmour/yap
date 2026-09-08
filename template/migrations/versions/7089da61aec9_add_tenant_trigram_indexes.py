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
    # tenants is a large, actively-queried table, so each index is built
    # `CONCURRENTLY` to avoid holding the write-blocking lock a plain
    # `CREATE INDEX` takes for the build's full duration. `CONCURRENTLY`
    # cannot run inside a transaction block, and `env.py` wraps every
    # migration in one, so each build runs in its own autocommit block,
    # which also means this migration no longer rolls back as a single
    # unit, an interrupted run leaves whatever ran so far committed, safe
    # to resume by re-running `upgrade head`.
    #
    # `IF NOT EXISTS` alone is not a safe retry: a build interrupted by a
    # crash or deploy timeout leaves an `INVALID` index under that name,
    # and `IF NOT EXISTS` only checks the name, not validity, so a retry
    # would silently skip rebuilding it. Dropping any same-named index
    # first (a no-op if the prior build succeeded, since then this
    # migration wouldn't be re-run) makes the create always build a
    # fresh, valid index.
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_tenants_name_trgm")
    with op.get_context().autocommit_block():
        # GIN trigram index on tenants.name (required for trigram ILIKE / %).
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_tenants_name_trgm "
            "ON tenants USING gin (name gin_trgm_ops)"
        )

    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_tenants_slug_trgm")
    with op.get_context().autocommit_block():
        # GIN trigram index on tenants.slug (required for trigram ILIKE / %).
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_tenants_slug_trgm "
            "ON tenants USING gin (slug gin_trgm_ops)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_tenants_slug_trgm")

    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_tenants_name_trgm")
