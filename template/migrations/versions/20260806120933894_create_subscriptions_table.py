"""Create `subscriptions` table

`tenant_id` is `NOT NULL` here (unlike most billing tables) — a
subscription only ever exists within a tenant's context, same pattern
as `AuditLog.tenant_id`.

Revision ID: 20260806120933894
Revises: 20260806120701321
Create Date: 2026-08-06 12:09:33.894000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "20260806120933894"
down_revision: Union[str, None] = "20260806120701321"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "trialing",
                "active",
                "past_due",
                "grace_period",
                "expired",
                "canceled",
                name="subscriptionstatus",
            ),
            nullable=False,
        ),
        sa.Column(
            "stripe_customer_id",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
        sa.Column(
            "stripe_subscription_id",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
        sa.Column("trial_started_at", sa.DateTime(), nullable=True),
        sa.Column("trial_ends_at", sa.DateTime(), nullable=True),
        sa.Column("current_period_start", sa.DateTime(), nullable=True),
        sa.Column("current_period_end", sa.DateTime(), nullable=True),
        sa.Column("grace_period_ends_at", sa.DateTime(), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False),
        sa.Column("canceled_at", sa.DateTime(), nullable=True),
        sa.Column("coupon_redemption_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_subscriptions_id"), "subscriptions", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_subscriptions_created_at"),
        "subscriptions",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_subscriptions_deleted_at"),
        "subscriptions",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_subscriptions_tenant_id"),
        "subscriptions",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_subscriptions_plan_id"), "subscriptions", ["plan_id"], unique=False
    )
    op.create_index(
        op.f("ix_subscriptions_stripe_customer_id"),
        "subscriptions",
        ["stripe_customer_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_subscriptions_stripe_subscription_id"),
        "subscriptions",
        ["stripe_subscription_id"],
        unique=False,
    )
    op.create_index(
        "ix_subscriptions_tenant_id_status",
        "subscriptions",
        ["tenant_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_subscriptions_tenant_id_status", table_name="subscriptions"
    )
    op.drop_index(
        op.f("ix_subscriptions_stripe_subscription_id"), table_name="subscriptions"
    )
    op.drop_index(
        op.f("ix_subscriptions_stripe_customer_id"), table_name="subscriptions"
    )
    op.drop_index(op.f("ix_subscriptions_plan_id"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_tenant_id"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_deleted_at"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_created_at"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_id"), table_name="subscriptions")
    op.drop_table("subscriptions")
    sa.Enum(name="subscriptionstatus").drop(op.get_bind(), checkfirst=True)
