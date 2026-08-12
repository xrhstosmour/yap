"""Audit log repository for logging operations.

This module provides the AuditLogRepository class for
creating and querying audit log entries.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import Any
from typing import cast
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import and_
from sqlmodel import select

from app.core.logging import get_logger
from app.models.audit_log import AuditAction
from app.models.audit_log import AuditLog
from app.repositories.base import BaseRepository

logger = get_logger("repository.audit")


def _jsonable(data: dict[str, Any] | None) -> dict[str, Any]:
    """Convert values to JSON-serializable primitives."""
    if not data:
        return {}

    serialized: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, UUID):
            serialized[key] = str(value)
        elif isinstance(value, datetime):
            serialized[key] = value.isoformat()
        elif isinstance(value, dict):
            serialized[key] = _jsonable(cast(dict[str, Any], value))
        elif isinstance(value, list):
            serialized[key] = [
                _jsonable(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            serialized[key] = value

    return serialized


class AuditLogRepository(BaseRepository[AuditLog]):
    """Repository for AuditLog model operations.

    Provides audit logging functionality for tracking
    user actions and system events.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize audit log repository.

        Args:
            session: Async database session
        """
        super().__init__(session, AuditLog)

    async def log(
        self,
        action: str | AuditAction,
        actor_id: str,
        actor_type: str,
        tenant_id: str | UUID,
        actor_email: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        changes: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = "success",
        error_message: str | None = None,
    ) -> AuditLog:
        """Create an audit log entry.

        Args:
            action: Action performed (e.g., "user_create")
            actor_id: ID of user/key performing action
            actor_type: Type of actor ("user" or "api_key")
            tenant_id: Tenant context
            actor_email: Email of actor (for display)
            resource_type: Type of resource affected
            resource_id: ID of affected resource
            changes: Before/after values
            metadata: Additional context (IP, user agent)
            status: Result ("success" or "failure")
            error_message: Error message if failed

        Returns:
            Created AuditLog instance
        """
        tenant_uuid = UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id

        return await self.create(
            {
                "action": action.value if isinstance(action, AuditAction) else action,
                "actor_id": str(actor_id),
                "actor_type": actor_type,
                "actor_email": actor_email,
                "tenant_id": tenant_uuid,
                "resource_type": resource_type,
                "resource_id": str(resource_id) if resource_id else None,
                "changes": _jsonable(changes),
                "extra_data": _jsonable(metadata),
                "status": status,
                "error_message": error_message,
            }
        )

    async def log_user_action(
        self,
        action: str | AuditAction,
        user_id: str | UUID,
        tenant_id: str | UUID,
        email: str | None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> AuditLog:
        """Log an action performed by a user.

        Convenience method for user-initiated actions.
        """
        return await self.log(
            action=action,
            actor_id=str(user_id),
            actor_type="user",
            actor_email=email,
            tenant_id=str(tenant_id),
            resource_type=resource_type,
            resource_id=resource_id,
            **kwargs,
        )

    async def log_user_action_safe(
        self,
        action: str | AuditAction,
        user_id: str | UUID,
        tenant_id: str | UUID,
        email: str | None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> None:
        """Log an action performed by a user, swallowing write failures.

        Use this at call sites where a transient audit-log write failure
        (e.g. a DB error) should not fail the surrounding business
        operation. The failure is logged with `exc_info` for visibility.
        """
        try:
            # A SAVEPOINT scopes the failure to just this insert: a plain
            # `session.rollback()` here would also discard whatever the
            # caller already flushed earlier in the same transaction (e.g.
            # a password change), silently undoing the operation this
            # method is supposed to leave unaffected. Without any rollback
            # at all, a failed flush leaves the session unusable until one
            # happens, so the *next* unrelated DB access on this session
            # raises `PendingRollbackError` instead.
            async with self.session.begin_nested():
                await self.log_user_action(
                    action=action,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    email=email,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    **kwargs,
                )
        except Exception:
            logger.warning(
                "audit_log_write_failed",
                action=action.value if isinstance(action, AuditAction) else action,
                resource_type=resource_type,
                resource_id=resource_id,
                exc_info=True,
            )

    async def log_api_key_action(
        self,
        action: str | AuditAction,
        api_key_id: str,
        tenant_id: str | UUID,
        resource_type: str | None = None,
        resource_id: str | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> AuditLog:
        """Log an action performed by an API key.

        Convenience method for API key-initiated actions.
        """
        return await self.log(
            action=action,
            actor_id=api_key_id,
            actor_type="api_key",
            tenant_id=str(tenant_id),
            resource_type=resource_type,
            resource_id=resource_id,
            **kwargs,
        )

    async def get_by_resource(
        self,
        resource_type: str,
        resource_id: str,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[AuditLog], int]:
        """Get audit logs for a specific resource.

        Args:
            resource_type: Type of resource
            resource_id: ID of resource
            skip: Pagination offset
            limit: Maximum results

        Returns:
            Tuple of (logs, total_count)
        """
        logs, total = await self.list(
            skip=skip,
            limit=limit,
            filters={
                "resource_type": resource_type,
                "resource_id": resource_id,
            },
            sort_by="created_at",
            sort_order="desc",
        )
        return list(logs), total

    async def get_by_actor(
        self,
        actor_id: str,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[AuditLog], int]:
        """Get audit logs for a specific actor.

        Args:
            actor_id: Actor ID
            skip: Pagination offset
            limit: Maximum results

        Returns:
            Tuple of (logs, total_count)
        """
        logs, total = await self.list(
            skip=skip,
            limit=limit,
            filters={"actor_id": actor_id},
            sort_by="created_at",
            sort_order="desc",
        )
        return list(logs), total

    async def get_recent_failures(
        self,
        hours: int = 24,
        limit: int = 100,
    ) -> list[AuditLog]:
        """Get recent failed actions, scoped to the current tenant.

        Useful for security monitoring.

        Args:
            hours: Look back period in hours
            limit: Maximum results

        Returns:
            List of failed audit logs
        """
        from app.core.tenant import get_current_tenant_id

        tenant_id = get_current_tenant_id()
        cutoff = datetime.now(UTC) - timedelta(hours=hours)

        query = (
            select(AuditLog)
            .where(
                and_(
                    AuditLog.created_at >= cutoff,  # type: ignore[arg-type]
                    AuditLog.status == "failure",  # type: ignore[arg-type]
                )
            )
            .order_by(AuditLog.created_at.desc())  # type: ignore[attr-defined]
            .limit(limit)
        )

        if tenant_id:
            query = query.where(AuditLog.tenant_id == tenant_id)  # type: ignore[arg-type]

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def cleanup_old_logs(self, days: int = 365) -> int:
        """Archive soft-delete old audit logs.

        Args:
            days: Delete logs older than this

        Returns:
            Number of logs archived
        """
        from app.core.tenant import get_current_tenant_id

        tenant_id = get_current_tenant_id()
        cutoff = datetime.now(UTC) - timedelta(days=days)

        query = (
            update(AuditLog)
            .where(
                and_(
                    AuditLog.created_at < cutoff,  # type: ignore[arg-type]
                    AuditLog.deleted_at.is_(None),  # type: ignore[union-attr]
                )
            )
            .values(deleted_at=datetime.now(UTC))
        )

        if tenant_id:
            query = query.where(AuditLog.tenant_id == tenant_id)  # type: ignore[arg-type]

        result = cast(CursorResult[Any], await self.session.execute(query))
        count = result.rowcount or 0

        await self.session.flush()

        logger.info("audit_logs_archived", count=count)
        return count
