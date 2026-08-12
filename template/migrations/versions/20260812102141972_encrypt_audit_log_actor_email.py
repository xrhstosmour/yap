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
    rows = bind.execute(
        sa.select(audit_logs_table.c.id, audit_logs_table.c.actor_email).where(
            audit_logs_table.c.actor_email.is_not(None)
        )
    ).fetchall()
    for row in rows:
        bind.execute(
            audit_logs_table.update()
            .where(audit_logs_table.c.id == row.id)
            .values(actor_email=crypto.encrypt(row.actor_email))
        )


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
    rows = bind.execute(
        sa.select(audit_logs_table.c.id, audit_logs_table.c.actor_email).where(
            audit_logs_table.c.actor_email.is_not(None)
        )
    ).fetchall()
    for row in rows:
        bind.execute(
            audit_logs_table.update()
            .where(audit_logs_table.c.id == row.id)
            .values(actor_email=crypto.decrypt(row.actor_email))
        )

    op.alter_column(
        "audit_logs",
        "actor_email",
        type_=sa.String(length=255),
        existing_type=sa.String(length=512),
    )
