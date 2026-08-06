"""Create `coupon_redemptions` table; add `subscriptions.coupon_redemption_id` FK

`CouponRedemption` FKs to both `coupons` and `subscriptions`, so it must
come after both exist. `subscriptions.coupon_redemption_id` was created
as a plain nullable column in `create_subscriptions_table` (before this
table existed to reference) — the FK constraint is added here.

Revision ID: 20260806121237158
Revises: 20260806120933894
Create Date: 2026-08-06 12:12:37.158000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260806121237158"
down_revision: Union[str, None] = "20260806120933894"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "coupon_redemptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("coupon_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=True),
        sa.Column("redeemed_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["coupon_id"], ["coupons.id"]),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        sa.ForeignKeyConstraint(["redeemed_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_coupon_redemptions_id"), "coupon_redemptions", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_coupon_redemptions_created_at"),
        "coupon_redemptions",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_coupon_redemptions_deleted_at"),
        "coupon_redemptions",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_coupon_redemptions_tenant_id"),
        "coupon_redemptions",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_coupon_redemptions_coupon_id"),
        "coupon_redemptions",
        ["coupon_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_coupon_redemptions_subscription_id"),
        "coupon_redemptions",
        ["subscription_id"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_subscriptions_coupon_redemption_id",
        "subscriptions",
        "coupon_redemptions",
        ["coupon_redemption_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_subscriptions_coupon_redemption_id",
        "subscriptions",
        type_="foreignkey",
    )

    op.drop_index(
        op.f("ix_coupon_redemptions_subscription_id"),
        table_name="coupon_redemptions",
    )
    op.drop_index(
        op.f("ix_coupon_redemptions_coupon_id"), table_name="coupon_redemptions"
    )
    op.drop_index(
        op.f("ix_coupon_redemptions_tenant_id"), table_name="coupon_redemptions"
    )
    op.drop_index(
        op.f("ix_coupon_redemptions_deleted_at"), table_name="coupon_redemptions"
    )
    op.drop_index(
        op.f("ix_coupon_redemptions_created_at"), table_name="coupon_redemptions"
    )
    op.drop_index(op.f("ix_coupon_redemptions_id"), table_name="coupon_redemptions")
    op.drop_table("coupon_redemptions")
