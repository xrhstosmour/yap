"""Create `payment_events` table

Deliberately no FKs — webhook dedup must never block on FK resolution.
Unique index on `(provider, provider_event_id)` is the actual dedup
mechanism.

Revision ID: 20260806121627398
Revises: 20260806121625398
Create Date: 2026-08-06 12:16:27.398000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "20260806121627398"
down_revision: Union[str, None] = "20260806121625398"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payment_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column(
            "provider", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False
        ),
        sa.Column(
            "provider_event_id",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column(
            "event_type",
            sqlmodel.sql.sqltypes.AutoString(length=200),
            nullable=False,
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("received", "processed", "failed", name="paymenteventstatus"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_payment_events_id"), "payment_events", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_payment_events_created_at"),
        "payment_events",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_events_deleted_at"),
        "payment_events",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_events_tenant_id"),
        "payment_events",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_events_event_type"),
        "payment_events",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        "ix_payment_events_provider_provider_event_id",
        "payment_events",
        ["provider", "provider_event_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_payment_events_provider_provider_event_id", table_name="payment_events"
    )
    op.drop_index(op.f("ix_payment_events_event_type"), table_name="payment_events")
    op.drop_index(op.f("ix_payment_events_tenant_id"), table_name="payment_events")
    op.drop_index(op.f("ix_payment_events_deleted_at"), table_name="payment_events")
    op.drop_index(op.f("ix_payment_events_created_at"), table_name="payment_events")
    op.drop_index(op.f("ix_payment_events_id"), table_name="payment_events")
    op.drop_table("payment_events")
    sa.Enum(name="paymenteventstatus").drop(op.get_bind(), checkfirst=True)
