"""Add compound indexes for common query patterns

Adds compound indexes on api_keys for list_by_user and
deactivate_expired_keys queries, a compound index on audit_logs
for get_recent_failures, and a single-column index on tenants.name
for ORDER BY and ILIKE searches.

Revision ID: b0b1c2d3e4f5
Revises: d2f4a6b8c0e1
Create Date: 2026-06-12 14:00:00.000000

"""

from typing import Sequence
from typing import Union

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "b0b1c2d3e4f5"
down_revision: Union[str, None] = "d2f4a6b8c0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # api_keys, audit_logs, and tenants are large, actively-queried tables, so
    # each index is built `CONCURRENTLY` to avoid holding the write-blocking
    # lock a plain `CREATE INDEX` takes for the build's full duration.
    # `CONCURRENTLY` cannot run inside a transaction block, and `env.py` wraps
    # every migration in one, so each build runs in its own autocommit block,
    # which also means this migration no longer rolls back as a single unit,
    # an interrupted run leaves whatever ran so far committed, safe to resume
    # by re-running `upgrade head`.
    #
    # `CREATE INDEX CONCURRENTLY IF NOT EXISTS` alone is not a safe retry: a
    # build interrupted by a crash or deploy timeout leaves an `INVALID`
    # index under that name, and `IF NOT EXISTS` only checks the name, not
    # validity, so a retry would silently skip rebuilding it. Dropping any
    # same-named index first (a no-op if the prior build succeeded, since
    # then this migration wouldn't be re-run) makes the create always build
    # a fresh, valid index.
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_api_keys_user_id_is_active")
    with op.get_context().autocommit_block():
        # list_by_user() and count_active_by_user() filter on
        # (user_id, is_active).
        op.create_index(
            op.f("ix_api_keys_user_id_is_active"),
            "api_keys",
            ["user_id", "is_active"],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )

    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_api_keys_expires_at_is_active"
        )
    with op.get_context().autocommit_block():
        # deactivate_expired_keys() scans (expires_at, is_active).
        op.create_index(
            op.f("ix_api_keys_expires_at_is_active"),
            "api_keys",
            ["expires_at", "is_active"],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )

    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_audit_logs_status_created_at"
        )
    with op.get_context().autocommit_block():
        # audit_logs: compound index for get_recent_failures()
        # WHERE status = 'failure' ORDER BY created_at DESC.
        op.create_index(
            op.f("ix_audit_logs_status_created_at"),
            "audit_logs",
            ["status", "created_at"],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )

    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_tenants_name")
    with op.get_context().autocommit_block():
        # tenants: single-column index for ORDER BY and ILIKE
        op.create_index(
            op.f("ix_tenants_name"),
            "tenants",
            ["name"],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            op.f("ix_tenants_name"),
            table_name="tenants",
            postgresql_concurrently=True,
            if_exists=True,
        )

    with op.get_context().autocommit_block():
        op.drop_index(
            op.f("ix_audit_logs_status_created_at"),
            table_name="audit_logs",
            postgresql_concurrently=True,
            if_exists=True,
        )

    with op.get_context().autocommit_block():
        op.drop_index(
            op.f("ix_api_keys_expires_at_is_active"),
            table_name="api_keys",
            postgresql_concurrently=True,
            if_exists=True,
        )

    with op.get_context().autocommit_block():
        op.drop_index(
            op.f("ix_api_keys_user_id_is_active"),
            table_name="api_keys",
            postgresql_concurrently=True,
            if_exists=True,
        )
