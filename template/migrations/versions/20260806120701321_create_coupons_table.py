"""Create `coupons` table

`Coupon` is a global catalog, not tenant-scoped — `tenant_id` is always
`NULL`. `CouponRedemption` (tenant-scoped) is created in a later
migration once `subscriptions` exists, since it FKs to both.

Revision ID: 20260806120701321
Revises: 20260806120428134
Create Date: 2026-08-06 12:07:01.321000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "20260806120701321"
down_revision: Union[str, None] = "20260806120428134"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "coupons",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column(
            "code", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False
        ),
        sa.Column(
            "stripe_coupon_id",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
        sa.Column(
            "stripe_promotion_code_id",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
        sa.Column(
            "discount_type",
            sa.Enum("percent", "free", "fixed_amount", name="coupondiscounttype"),
            nullable=False,
        ),
        sa.Column("percent_off", sa.Integer(), nullable=True),
        sa.Column("amount_off_cents", sa.Integer(), nullable=True),
        sa.Column(
            "duration",
            sa.Enum("once", "repeating", "forever", name="couponduration"),
            nullable=False,
        ),
        sa.Column("duration_in_months", sa.Integer(), nullable=True),
        sa.Column("free_days", sa.Integer(), nullable=False),
        sa.Column("max_redemptions", sa.Integer(), nullable=True),
        sa.Column("redemption_count", sa.Integer(), nullable=False),
        sa.Column("valid_from", sa.DateTime(), nullable=True),
        sa.Column("valid_until", sa.DateTime(), nullable=True),
        sa.Column("allowed_plan_ids", sa.JSON(), nullable=True),
        sa.Column("allowed_tenant_ids", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_coupons_id"), "coupons", ["id"], unique=False)
    op.create_index(
        op.f("ix_coupons_created_at"), "coupons", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_coupons_deleted_at"), "coupons", ["deleted_at"], unique=False
    )
    op.create_index(op.f("ix_coupons_tenant_id"), "coupons", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_coupons_code"), "coupons", ["code"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_coupons_code"), table_name="coupons")
    op.drop_index(op.f("ix_coupons_tenant_id"), table_name="coupons")
    op.drop_index(op.f("ix_coupons_deleted_at"), table_name="coupons")
    op.drop_index(op.f("ix_coupons_created_at"), table_name="coupons")
    op.drop_index(op.f("ix_coupons_id"), table_name="coupons")
    op.drop_table("coupons")
    sa.Enum(name="couponduration").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="coupondiscounttype").drop(op.get_bind(), checkfirst=True)
