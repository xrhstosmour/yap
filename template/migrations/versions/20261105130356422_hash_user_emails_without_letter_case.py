"""Rehash `users.email_hash` without regard to the letter case of the address

Rows written before `hash_for_email` hold the hash of the address as it was
typed, so `Alex@x.com` and `alex@x.com` could both register. This rewrites each
to the hash of the lowercase address. Where two rows are one mailbox, the one
that gets there first keeps the lowercase hash and the other keeps the hash it
had, because the unique index would refuse both, and a lookup that matches
both takes the older, see `UserRepository.get_by_email`.

Revision ID: 20261105130356422
Revises: 20261022205207292
Create Date: 2026-11-05 13:03:56.000000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20261105130356422"
down_revision: Union[str, None] = "20261022205207292"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Keyset page size, see the migration that added `email_hash`.
_BATCH_SIZE = 1000


def upgrade() -> None:
    from app.core.encryption import crypto

    bind = op.get_bind()
    users_table = sa.table(
        "users",
        sa.column("id", sa.Uuid()),
        sa.column("email", sa.String()),
        sa.column("email_hash", sa.String()),
        sa.column("created_at", sa.DateTime()),
    )

    # Oldest first, so that of two rows with one mailbox the older is the one
    # that gets the lowercase hash, unless the other already holds it.
    last_key = None
    while True:
        query = (
            sa.select(
                users_table.c.id,
                users_table.c.email,
                users_table.c.email_hash,
                users_table.c.created_at,
            )
            .order_by(users_table.c.created_at, users_table.c.id)
            .limit(_BATCH_SIZE)
        )
        if last_key is not None:
            query = query.where(
                sa.tuple_(users_table.c.created_at, users_table.c.id) > last_key
            )
        rows = bind.execute(query).fetchall()
        if not rows:
            break

        for row in rows:
            # Erased rows hold an empty email and a random token's hash.
            if not row.email:
                continue
            email = crypto.decrypt(row.email)
            new_hash = crypto.hash_for_email(email)
            if new_hash == row.email_hash:
                continue
            taken = bind.execute(
                sa.select(users_table.c.id).where(
                    users_table.c.email_hash == new_hash,
                    users_table.c.id != row.id,
                )
            ).first()
            if taken is None:
                bind.execute(
                    users_table.update()
                    .where(users_table.c.id == row.id)
                    .values(email_hash=new_hash)
                )

        last_key = (rows[-1].created_at, rows[-1].id)
        if len(rows) < _BATCH_SIZE:
            break


def downgrade() -> None:
    # The hashes cannot be told apart from the ones written as typed, and
    # both are matched on lookup, so there is nothing to undo.
    pass
