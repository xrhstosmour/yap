"""Drop the trigram index on users.email.

`e3c4b5a6d7f8` indexed `immutable_unaccent(email)` for search, and
`20260731174425314` later encrypted `email` with Fernet without dropping
it. The column now holds randomized ciphertext, search leaves email out
for that reason and lookups go through `email_hash`, so no query can use
the index, while every insert and every email change still writes the
trigrams of about a hundred random characters into it.

Revision ID: 20261022121731764
Revises: 20261007061302248
Create Date: 2026-10-22 12:17:31.764000

"""

from typing import Sequence
from typing import Union

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "20261022121731764"
down_revision: Union[str, None] = "20261007061302248"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `CONCURRENTLY` so dropping it never blocks writes to users, which
    # cannot run inside the transaction `env.py` wraps each migration in.
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_users_email_trgm")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_users_email_trgm "
            "ON users USING gin (public.immutable_unaccent(email) gin_trgm_ops)"
        )
