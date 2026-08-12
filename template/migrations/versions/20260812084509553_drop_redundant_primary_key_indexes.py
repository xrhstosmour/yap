"""Drop redundant secondary indexes on primary key columns

Revision ID: 20260812084509553
Revises: 20260731171240225
Create Date: 2026-08-12T08:45:09.553000+00:00

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260812084509553"
down_revision: Union[str, None] = "20260731171240225"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Every `BaseModel`-derived table's `id` column had `index=True` alongside
# `primary_key=True`, which asks the database for a second index on a column
# that already has one from the `PRIMARY KEY` constraint. Wasted storage and
# write amplification, zero query benefit: nothing can use the second index
# that the constraint's own index doesn't already serve.
TABLES = [
    "feature_flags",
    "tenants",
    "audit_logs",
    "users",
    "api_keys",
    "totp_recovery_codes",
    "oauth_accounts",
    "graveyard",
    "outbox_events",
]


def upgrade() -> None:
    for table in TABLES:
        op.drop_index(op.f(f"ix_{table}_id"), table_name=table)


def downgrade() -> None:
    for table in TABLES:
        op.create_index(op.f(f"ix_{table}_id"), table, ["id"], unique=False)
