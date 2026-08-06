"""Create `invoice_sequences` and `invoices` tables

`invoice_sequences` is the global, gapless invoice-number counter
(`tenant_id` always `NULL`, see `app.models.invoice` module docstring).
`invoices` FKs to `subscriptions`.

Revision ID: 20260806121359091
Revises: 20260806121237158
Create Date: 2026-08-06 12:13:59.091000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "20260806121359091"
down_revision: Union[str, None] = "20260806121237158"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invoice_sequences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column(
            "series", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False
        ),
        sa.Column("next_number", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_invoice_sequences_id"), "invoice_sequences", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_invoice_sequences_created_at"),
        "invoice_sequences",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_invoice_sequences_deleted_at"),
        "invoice_sequences",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_invoice_sequences_tenant_id"),
        "invoice_sequences",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_invoice_sequences_series"),
        "invoice_sequences",
        ["series"],
        unique=True,
    )

    op.create_table(
        "invoices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("subscription_id", sa.Uuid(), nullable=True),
        sa.Column(
            "stripe_invoice_id",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
        sa.Column(
            "invoice_number",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "draft", "open", "paid", "void", "uncollectible", name="invoicestatus"
            ),
            nullable=False,
        ),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("amount_due_cents", sa.Integer(), nullable=False),
        sa.Column("amount_paid_cents", sa.Integer(), nullable=False),
        sa.Column(
            "currency", sqlmodel.sql.sqltypes.AutoString(length=3), nullable=False
        ),
        sa.Column("vat_rate", sa.Numeric(5, 4), nullable=False),
        sa.Column("vat_amount_cents", sa.Integer(), nullable=False),
        sa.Column(
            "vat_id", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True
        ),
        sa.Column(
            "customer_country",
            sqlmodel.sql.sqltypes.AutoString(length=2),
            nullable=True,
        ),
        sa.Column("reverse_charge", sa.Boolean(), nullable=False),
        sa.Column(
            "billing_name",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
        sa.Column("billing_address", sa.JSON(), nullable=False),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column(
            "hosted_invoice_url",
            sqlmodel.sql.sqltypes.AutoString(length=1024),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_invoices_id"), "invoices", ["id"], unique=False)
    op.create_index(
        op.f("ix_invoices_created_at"), "invoices", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_invoices_deleted_at"), "invoices", ["deleted_at"], unique=False
    )
    op.create_index(op.f("ix_invoices_tenant_id"), "invoices", ["tenant_id"], unique=False)
    op.create_index(
        op.f("ix_invoices_subscription_id"), "invoices", ["subscription_id"], unique=False
    )
    op.create_index(
        op.f("ix_invoices_stripe_invoice_id"),
        "invoices",
        ["stripe_invoice_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_invoices_invoice_number"), "invoices", ["invoice_number"], unique=True
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_invoices_invoice_number"), table_name="invoices")
    op.drop_index(op.f("ix_invoices_stripe_invoice_id"), table_name="invoices")
    op.drop_index(op.f("ix_invoices_subscription_id"), table_name="invoices")
    op.drop_index(op.f("ix_invoices_tenant_id"), table_name="invoices")
    op.drop_index(op.f("ix_invoices_deleted_at"), table_name="invoices")
    op.drop_index(op.f("ix_invoices_created_at"), table_name="invoices")
    op.drop_index(op.f("ix_invoices_id"), table_name="invoices")
    op.drop_table("invoices")
    sa.Enum(name="invoicestatus").drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f("ix_invoice_sequences_series"), table_name="invoice_sequences")
    op.drop_index(
        op.f("ix_invoice_sequences_tenant_id"), table_name="invoice_sequences"
    )
    op.drop_index(
        op.f("ix_invoice_sequences_deleted_at"), table_name="invoice_sequences"
    )
    op.drop_index(
        op.f("ix_invoice_sequences_created_at"), table_name="invoice_sequences"
    )
    op.drop_index(op.f("ix_invoice_sequences_id"), table_name="invoice_sequences")
    op.drop_table("invoice_sequences")
