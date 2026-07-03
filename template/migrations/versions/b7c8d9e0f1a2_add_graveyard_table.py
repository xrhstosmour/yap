"""Add graveyard table

The graveyard model existed without a migration, so the table was only created
by SQLModel metadata in SQLite tests and never provisioned by migrations. This
adds it so alembic upgrade head builds the full schema.

Revision ID: b7c8d9e0f1a2
Revises: d2f4a6b8c0e1
Create Date: 2026-07-03 18:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlmodel.sql.sqltypes import AutoString


# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "d2f4a6b8c0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "graveyard",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("model_name", AutoString(length=100), nullable=False),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("deleted_by", AutoString(length=100), nullable=False),
        sa.Column("record_deleted_at", sa.DateTime(), nullable=False),
        sa.Column("reason", AutoString(length=500), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_graveyard_created_at"),
        "graveyard",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_graveyard_deleted_at"),
        "graveyard",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_graveyard_id"),
        "graveyard",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_graveyard_model_name"),
        "graveyard",
        ["model_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_graveyard_record_deleted_at"),
        "graveyard",
        ["record_deleted_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_graveyard_record_id"),
        "graveyard",
        ["record_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_graveyard_tenant_id"),
        "graveyard",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_graveyard_tenant_id"), table_name="graveyard")
    op.drop_index(op.f("ix_graveyard_record_id"), table_name="graveyard")
    op.drop_index(op.f("ix_graveyard_record_deleted_at"), table_name="graveyard")
    op.drop_index(op.f("ix_graveyard_model_name"), table_name="graveyard")
    op.drop_index(op.f("ix_graveyard_id"), table_name="graveyard")
    op.drop_index(op.f("ix_graveyard_deleted_at"), table_name="graveyard")
    op.drop_index(op.f("ix_graveyard_created_at"), table_name="graveyard")
    op.drop_table("graveyard")
