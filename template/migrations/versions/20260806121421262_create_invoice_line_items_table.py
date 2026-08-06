"""Create `invoice_line_items` table

Revision ID: 20260806121421262
Revises: 20260806121359091
Create Date: 2026-08-06 12:14:21.262000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "20260806121421262"
down_revision: Union[str, None] = "20260806121359091"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invoice_line_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column(
            "description",
            sqlmodel.sql.sqltypes.AutoString(length=500),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_amount_cents", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_invoice_line_items_id"), "invoice_line_items", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_invoice_line_items_created_at"),
        "invoice_line_items",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_invoice_line_items_deleted_at"),
        "invoice_line_items",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_invoice_line_items_tenant_id"),
        "invoice_line_items",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_invoice_line_items_invoice_id"),
        "invoice_line_items",
        ["invoice_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_invoice_line_items_invoice_id"), table_name="invoice_line_items"
    )
    op.drop_index(
        op.f("ix_invoice_line_items_tenant_id"), table_name="invoice_line_items"
    )
    op.drop_index(
        op.f("ix_invoice_line_items_deleted_at"), table_name="invoice_line_items"
    )
    op.drop_index(
        op.f("ix_invoice_line_items_created_at"), table_name="invoice_line_items"
    )
    op.drop_index(op.f("ix_invoice_line_items_id"), table_name="invoice_line_items")
    op.drop_table("invoice_line_items")
