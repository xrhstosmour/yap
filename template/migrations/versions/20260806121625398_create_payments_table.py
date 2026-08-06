"""Create `payments` table

Revision ID: 20260806121625398
Revises: 20260806121623398
Create Date: 2026-08-06 12:16:25.398000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "20260806121625398"
down_revision: Union[str, None] = "20260806121623398"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("invoice_id", sa.Uuid(), nullable=True),
        sa.Column("subscription_id", sa.Uuid(), nullable=True),
        sa.Column("payment_method_id", sa.Uuid(), nullable=True),
        sa.Column(
            "provider", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False
        ),
        sa.Column(
            "stripe_payment_intent_id",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
        sa.Column(
            "stripe_charge_id",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column(
            "currency", sqlmodel.sql.sqltypes.AutoString(length=3), nullable=False
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "succeeded",
                "failed",
                "refunded",
                "partially_refunded",
                name="paymentstatus",
            ),
            nullable=False,
        ),
        sa.Column(
            "failure_code",
            sqlmodel.sql.sqltypes.AutoString(length=100),
            nullable=True,
        ),
        sa.Column(
            "failure_message",
            sqlmodel.sql.sqltypes.AutoString(length=1000),
            nullable=True,
        ),
        sa.Column("refunded_amount_cents", sa.Integer(), nullable=False),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("refunded_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        sa.ForeignKeyConstraint(["payment_method_id"], ["payment_methods.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_payments_id"), "payments", ["id"], unique=False)
    op.create_index(
        op.f("ix_payments_created_at"), "payments", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_payments_deleted_at"), "payments", ["deleted_at"], unique=False
    )
    op.create_index(op.f("ix_payments_tenant_id"), "payments", ["tenant_id"], unique=False)
    op.create_index(
        "ix_payments_tenant_id_created_at", "payments", ["tenant_id", "created_at"]
    )
    op.create_index(
        "ix_payments_subscription_id_status", "payments", ["subscription_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_payments_subscription_id_status", table_name="payments")
    op.drop_index("ix_payments_tenant_id_created_at", table_name="payments")
    op.drop_index(op.f("ix_payments_tenant_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_deleted_at"), table_name="payments")
    op.drop_index(op.f("ix_payments_created_at"), table_name="payments")
    op.drop_index(op.f("ix_payments_id"), table_name="payments")
    op.drop_table("payments")
    sa.Enum(name="paymentstatus").drop(op.get_bind(), checkfirst=True)
