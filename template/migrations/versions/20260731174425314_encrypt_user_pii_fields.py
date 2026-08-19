"""Encrypt `email`/`phone` on `users` and add deterministic search hashes

`full_name` is intentionally left unencrypted, see the note on
`app.models.user.User` for why full trigram/FTS search over encrypted
text is not viable with the HMAC-based scheme used here.

Revision ID: 20260731174425314
Revises: 7089da61aec9
Create Date: 2026-07-31 17:44:25.314000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260731174425314"
down_revision: Union[str, None] = "7089da61aec9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.core.encryption import crypto

    bind = op.get_bind()

    # 1. Add the deterministic HMAC hash columns, nullable for now,
    #    they are backfilled below before being locked down.
    op.add_column("users", sa.Column("email_hash", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("phone_hash", sa.String(length=64), nullable=True))

    # 2. Widen storage for the Fernet ciphertext, which is substantially
    #    longer than the plaintext it replaces.
    op.alter_column(
        "users",
        "email",
        type_=sa.String(length=512),
        existing_type=sa.String(length=255),
    )
    op.alter_column(
        "users",
        "phone",
        type_=sa.String(length=255),
        existing_type=sa.String(length=16),
    )

    # 3. Encrypt existing plaintext rows in place and compute their search
    #    hashes. Fernet/HMAC are application-layer primitives (not pure
    #    SQL), so this runs row-by-row in Python rather than via UPDATE.
    users_table = sa.table(
        "users",
        sa.column("id", sa.Uuid()),
        sa.column("email", sa.String()),
        sa.column("phone", sa.String()),
        sa.column("email_hash", sa.String()),
        sa.column("phone_hash", sa.String()),
    )
    rows = bind.execute(
        sa.select(users_table.c.id, users_table.c.email, users_table.c.phone)
    ).fetchall()
    for row in rows:
        values: dict[str, str] = {
            "email": crypto.encrypt(row.email),
            "email_hash": crypto.hash_for_search(row.email),
        }
        if row.phone:
            values["phone"] = crypto.encrypt(row.phone)
            values["phone_hash"] = crypto.hash_for_search(row.phone)
        bind.execute(
            users_table.update().where(users_table.c.id == row.id).values(**values)
        )

    # 4. Drop the plaintext-era unique index. Fernet ciphertext is
    #    randomised per encryption call, so it can never be compared or
    #    indexed directly, equality lookups now go through email_hash.
    op.drop_index(op.f("ix_users_email"), table_name="users")

    # 5. Lock down the backfilled hash columns: email_hash is required
    #    and unique (mirrors the old email uniqueness constraint);
    #    phone_hash stays nullable since phone itself is optional.
    op.alter_column("users", "email_hash", nullable=False)
    op.create_index(op.f("ix_users_email_hash"), "users", ["email_hash"], unique=True)
    op.create_index(op.f("ix_users_phone_hash"), "users", ["phone_hash"], unique=False)


def downgrade() -> None:
    from app.core.encryption import crypto

    bind = op.get_bind()

    op.drop_index(op.f("ix_users_phone_hash"), table_name="users")
    op.drop_index(op.f("ix_users_email_hash"), table_name="users")

    # Decrypt existing rows back to plaintext before shrinking the
    # column widths, so no ciphertext gets truncated in the process.
    users_table = sa.table(
        "users",
        sa.column("id", sa.Uuid()),
        sa.column("email", sa.String()),
        sa.column("phone", sa.String()),
    )
    rows = bind.execute(
        sa.select(users_table.c.id, users_table.c.email, users_table.c.phone)
    ).fetchall()
    for row in rows:
        values: dict[str, str] = {"email": crypto.decrypt(row.email)}
        if row.phone:
            values["phone"] = crypto.decrypt(row.phone)
        bind.execute(
            users_table.update().where(users_table.c.id == row.id).values(**values)
        )

    op.alter_column(
        "users",
        "phone",
        type_=sa.String(length=16),
        existing_type=sa.String(length=255),
    )
    op.alter_column(
        "users",
        "email",
        type_=sa.String(length=255),
        existing_type=sa.String(length=512),
    )

    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.drop_column("users", "phone_hash")
    op.drop_column("users", "email_hash")
