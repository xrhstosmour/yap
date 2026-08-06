"""Create `plans` table

`Plan` is a global billing catalog, not tenant-scoped — `tenant_id` is
always `NULL` here. See `app.repositories.plan_repository.PlanRepository`
for the deliberate tenant-filter bypass this requires.

Revision ID: 20260806120428134
Revises: 20260806115848737
Create Date: 2026-08-06 12:04:28.134000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "20260806120428134"
down_revision: Union[str, None] = "20260806115848737"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column(
            "name", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False
        ),
        sa.Column(
            "stripe_price_id",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
        sa.Column(
            "stripe_product_id",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column(
            "currency", sqlmodel.sql.sqltypes.AutoString(length=3), nullable=False
        ),
        sa.Column(
            "billing_interval",
            sa.Enum("month", "year", name="billinginterval"),
            nullable=False,
        ),
        sa.Column("trial_days", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("features", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_plans_id"), "plans", ["id"], unique=False)
    op.create_index(op.f("ix_plans_created_at"), "plans", ["created_at"], unique=False)
    op.create_index(op.f("ix_plans_deleted_at"), "plans", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_plans_tenant_id"), "plans", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_plans_name"), "plans", ["name"], unique=True)
    op.create_index(
        op.f("ix_plans_stripe_price_id"), "plans", ["stripe_price_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_plans_stripe_price_id"), table_name="plans")
    op.drop_index(op.f("ix_plans_name"), table_name="plans")
    op.drop_index(op.f("ix_plans_tenant_id"), table_name="plans")
    op.drop_index(op.f("ix_plans_deleted_at"), table_name="plans")
    op.drop_index(op.f("ix_plans_created_at"), table_name="plans")
    op.drop_index(op.f("ix_plans_id"), table_name="plans")
    op.drop_table("plans")
    sa.Enum(name="billinginterval").drop(op.get_bind(), checkfirst=True)
