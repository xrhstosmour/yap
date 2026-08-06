"""Add unique constraint on `invoices.stripe_invoice_id`

Prevents a second `invoice.paid` webhook event mapping to the same
Stripe invoice from minting a duplicate sequential invoice number or
duplicate `Payment` row. `NULL` values remain unconstrained (Postgres
treats them as distinct), so draft/manually-created invoices with no
Stripe counterpart are unaffected.

Revision ID: 20260806202450953
Revises: 20260806121627398
Create Date: 2026-08-06 20:24:50.953000+00:00

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260806202450953"
down_revision: Union[str, None] = "20260806121627398"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(op.f("ix_invoices_stripe_invoice_id"), table_name="invoices")
    op.create_index(
        op.f("ix_invoices_stripe_invoice_id"),
        "invoices",
        ["stripe_invoice_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_invoices_stripe_invoice_id"), table_name="invoices")
    op.create_index(
        op.f("ix_invoices_stripe_invoice_id"),
        "invoices",
        ["stripe_invoice_id"],
        unique=False,
    )
