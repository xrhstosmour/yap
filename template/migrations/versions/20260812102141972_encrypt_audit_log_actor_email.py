"""Encrypt `actor_email` on `audit_logs`

No search hash, unlike the `users.email`/`users.phone` encryption
(20260731174425314): nothing ever looks an audit log up by actor_email,
only actor_id, so there is no equality-lookup need to trade off.

Revision ID: 20260812102141972
Revises: 20260812084509553
Create Date: 2026-08-12T10:21:41.972000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260812102141972"
down_revision: Union[str, None] = "20260812084509553"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Keyset page size. `env.py` wraps the whole migration in one transaction, so
# this cannot shrink lock duration, but it bounds the row-processing loop to
# one page in memory at a time instead of `fetchall()`-ing the entire table.
_BATCH_SIZE = 1000


def upgrade() -> None:
    from app.core.encryption import crypto

    bind = op.get_bind()

    # Widen storage for the Fernet ciphertext, which is substantially
    # longer than the plaintext it replaces.
    op.alter_column(
        "audit_logs",
        "actor_email",
        type_=sa.String(length=512),
        existing_type=sa.String(length=255),
    )

    # Encrypt existing plaintext rows in place. Fernet is an
    # application-layer primitive (not pure SQL), so this runs row-by-row
    # in Python rather than via UPDATE.
    audit_logs_table = sa.table(
        "audit_logs",
        sa.column("id", sa.Uuid()),
        sa.column("actor_email", sa.String()),
    )
    last_id = None
    while True:
        query = (
            sa.select(audit_logs_table.c.id, audit_logs_table.c.actor_email)
            .where(audit_logs_table.c.actor_email.is_not(None))
            .order_by(audit_logs_table.c.id)
            .limit(_BATCH_SIZE)
        )
        if last_id is not None:
            query = query.where(audit_logs_table.c.id > last_id)
        rows = bind.execute(query).fetchall()
        if not rows:
            break

        for row in rows:
            bind.execute(
                audit_logs_table.update()
                .where(audit_logs_table.c.id == row.id)
                .values(actor_email=crypto.encrypt(row.actor_email))
            )

        last_id = rows[-1].id
        if len(rows) < _BATCH_SIZE:
            break


def downgrade() -> None:
    from app.core.encryption import crypto

    bind = op.get_bind()

    # Decrypt existing rows back to plaintext before shrinking the column
    # width, so no ciphertext gets truncated in the process.
    audit_logs_table = sa.table(
        "audit_logs",
        sa.column("id", sa.Uuid()),
        sa.column("actor_email", sa.String()),
    )
    last_id = None
    while True:
        query = (
            sa.select(audit_logs_table.c.id, audit_logs_table.c.actor_email)
            .where(audit_logs_table.c.actor_email.is_not(None))
            .order_by(audit_logs_table.c.id)
            .limit(_BATCH_SIZE)
        )
        if last_id is not None:
            query = query.where(audit_logs_table.c.id > last_id)
        rows = bind.execute(query).fetchall()
        if not rows:
            break

        for row in rows:
            bind.execute(
                audit_logs_table.update()
                .where(audit_logs_table.c.id == row.id)
                .values(actor_email=crypto.decrypt(row.actor_email))
            )

        last_id = rows[-1].id
        if len(rows) < _BATCH_SIZE:
            break

    op.alter_column(
        "audit_logs",
        "actor_email",
        type_=sa.String(length=255),
        existing_type=sa.String(length=512),
    )
