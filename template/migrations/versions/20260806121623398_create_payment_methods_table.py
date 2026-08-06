"""Create `payment_methods` table

Revision ID: 20260806121623398
Revises: 20260806121421262
Create Date: 2026-08-06 12:16:23.398000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "20260806121623398"
down_revision: Union[str, None] = "20260806121421262"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payment_methods",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column(
            "stripe_payment_method_id",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column(
            "type",
            sa.Enum("card", "sepa_debit", name="paymentmethodtype"),
            nullable=False,
        ),
        sa.Column(
            "brand", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True
        ),
        sa.Column(
            "last_four", sqlmodel.sql.sqltypes.AutoString(length=4), nullable=True
        ),
        sa.Column("exp_month", sa.Integer(), nullable=True),
        sa.Column("exp_year", sa.Integer(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_payment_methods_id"), "payment_methods", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_payment_methods_created_at"),
        "payment_methods",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_methods_deleted_at"),
        "payment_methods",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_methods_tenant_id"),
        "payment_methods",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_methods_stripe_payment_method_id"),
        "payment_methods",
        ["stripe_payment_method_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_payment_methods_stripe_payment_method_id"),
        table_name="payment_methods",
    )
    op.drop_index(
        op.f("ix_payment_methods_tenant_id"), table_name="payment_methods"
    )
    op.drop_index(
        op.f("ix_payment_methods_deleted_at"), table_name="payment_methods"
    )
    op.drop_index(
        op.f("ix_payment_methods_created_at"), table_name="payment_methods"
    )
    op.drop_index(op.f("ix_payment_methods_id"), table_name="payment_methods")
    op.drop_table("payment_methods")
    sa.Enum(name="paymentmethodtype").drop(op.get_bind(), checkfirst=True)
