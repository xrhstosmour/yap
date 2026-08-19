"""Replace single-column indexes with compound indexes; add outbox_events table

Drops the redundant single-column indexes on audit_logs.actor_id and
audit_logs.tenant_id, replacing them with compound (col, created_at)
indexes that eliminate post-scan sort passes on the most common queries.
Also creates the outbox_events table (missing from the initial schema)
with a (status, created_at) compound index instead of a single-column
status index, so the dispatcher's get_pending() query is index-only.

Revision ID: c9d1e3f5a7b2
Revises: a1b2c3d4e5f6
Create Date: 2026-06-04 13:00:00.000000

"""

from typing import Sequence
from typing import Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "c9d1e3f5a7b2"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # audit_logs: replace single-column indexes with compound indexes

    # Drop the single-column indexes; the compound indexes cover leading-column
    # lookups so no existing query loses index support.
    op.drop_index(op.f("ix_audit_logs_actor_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_tenant_id"), table_name="audit_logs")

    # Compound (actor_id, created_at), GDPR export and user activity queries.
    op.create_index(
        op.f("ix_audit_logs_actor_id_created_at"),
        "audit_logs",
        ["actor_id", "created_at"],
        unique=False,
    )
    # Compound (tenant_id, created_at), tenant audit timeline queries.
    op.create_index(
        op.f("ix_audit_logs_tenant_id_created_at"),
        "audit_logs",
        ["tenant_id", "created_at"],
        unique=False,
    )

    # outbox_events: create missing table with compound index

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column(
            "event_type",
            sqlmodel.sql.sqltypes.AutoString(length=200),
            nullable=False,
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sqlmodel.sql.sqltypes.AutoString(length=20),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_outbox_events_id"), "outbox_events", ["id"], unique=False)
    op.create_index(
        op.f("ix_outbox_events_created_at"),
        "outbox_events",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_outbox_events_deleted_at"),
        "outbox_events",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_outbox_events_tenant_id"),
        "outbox_events",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_outbox_events_event_type"),
        "outbox_events",
        ["event_type"],
        unique=False,
    )
    # Compound (status, created_at), dispatcher's get_pending() query.
    op.create_index(
        op.f("ix_outbox_events_status_created_at"),
        "outbox_events",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    # outbox_events
    op.drop_index(
        op.f("ix_outbox_events_status_created_at"), table_name="outbox_events"
    )
    op.drop_index(op.f("ix_outbox_events_event_type"), table_name="outbox_events")
    op.drop_index(op.f("ix_outbox_events_tenant_id"), table_name="outbox_events")
    op.drop_index(op.f("ix_outbox_events_deleted_at"), table_name="outbox_events")
    op.drop_index(op.f("ix_outbox_events_created_at"), table_name="outbox_events")
    op.drop_index(op.f("ix_outbox_events_id"), table_name="outbox_events")
    op.drop_table("outbox_events")

    # audit_logs: restore single-column indexes
    op.drop_index(op.f("ix_audit_logs_tenant_id_created_at"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_actor_id_created_at"), table_name="audit_logs")
    op.create_index(
        op.f("ix_audit_logs_tenant_id"), "audit_logs", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_audit_logs_actor_id"), "audit_logs", ["actor_id"], unique=False
    )
