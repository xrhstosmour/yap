"""Add compound indexes for common query patterns

Adds compound indexes on api_keys for list_by_user and
deactivate_expired_keys queries, a compound index on audit_logs
for get_recent_failures, and a single-column index on tenants.name
for ORDER BY and ILIKE searches.

Revision ID: b0b1c2d3e4f5
Revises: c9d1e3f5a7b2
Create Date: 2026-06-12 14:00:00.000000

"""

from typing import Sequence
from typing import Union

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "b0b1c2d3e4f5"
down_revision: Union[str, None] = "c9d1e3f5a7b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # api_keys: compound indexes for common query patterns

    # list_by_user() and count_active_by_user() filter on (user_id, is_active).
    op.create_index(
        op.f("ix_api_keys_user_id_is_active"),
        "api_keys",
        ["user_id", "is_active"],
        unique=False,
    )
    # deactivate_expired_keys() scans (expires_at, is_active).
    op.create_index(
        op.f("ix_api_keys_expires_at_is_active"),
        "api_keys",
        ["expires_at", "is_active"],
        unique=False,
    )

    # audit_logs: compound index for get_recent_failures()
    # WHERE status = 'failure' ORDER BY created_at DESC.
    op.create_index(
        op.f("ix_audit_logs_status_created_at"),
        "audit_logs",
        ["status", "created_at"],
        unique=False,
    )

    # tenants: single-column index for ORDER BY and ILIKE
    op.create_index(
        op.f("ix_tenants_name"),
        "tenants",
        ["name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_tenants_name"), table_name="tenants")
    op.drop_index(
        op.f("ix_audit_logs_status_created_at"), table_name="audit_logs"
    )
    op.drop_index(
        op.f("ix_api_keys_expires_at_is_active"), table_name="api_keys"
    )
    op.drop_index(
        op.f("ix_api_keys_user_id_is_active"), table_name="api_keys"
    )
