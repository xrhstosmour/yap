"""Index users.full_name for full-text search.

User search runs `to_tsvector('simple', immutable_unaccent(full_name))` for
any query of three characters or more, and nothing indexed that, so every
such search and its count read the whole table. The trigram index from
`e3c4b5a6d7f8` only serves the shorter, trigram queries.

Built for the default `FTS_LANGUAGE`, `simple`. A project that changes it
needs the same index with its own configuration, the planner only uses an
index whose expression matches the query's.

Revision ID: 20261022130759210
Revises: 20261022121731764
Create Date: 2026-10-22 13:07:59.210000

"""

from typing import Sequence
from typing import Union

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "20261022130759210"
down_revision: Union[str, None] = "20261022121731764"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `CONCURRENTLY` so building it never blocks writes to users, which
    # cannot run inside the transaction `env.py` wraps each migration in.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_users_full_name_fts "
            "ON users USING gin "
            "(to_tsvector('simple'::regconfig, public.immutable_unaccent(full_name)))"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_users_full_name_fts")
