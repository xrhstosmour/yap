"""Enforce at most one non-terminal `Subscription` per tenant

Partial unique index on `(tenant_id)` restricted to the non-terminal
statuses (`trialing`, `active`, `past_due`, `grace_period`) — mirrors
`app.models.subscription.NON_TERMINAL_STATUSES`. Terminal rows
(`expired`, `canceled`) are excluded so they can accumulate as history
without conflicting with a later resubscription's new row.

Revision ID: 20260806202452058
Revises: 20260806202450953
Create Date: 2026-08-06 20:24:52.058000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260806202452058"
down_revision: Union[str, None] = "20260806202450953"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX_NAME = "ix_subscriptions_tenant_id_one_non_terminal"


def upgrade() -> None:
    op.create_index(
        _INDEX_NAME,
        "subscriptions",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('trialing', 'active', 'past_due', 'grace_period')"
        ),
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="subscriptions")
