"""Cleanup and maintenance tasks.

This module defines background tasks for cleanup and maintenance.
"""

from __future__ import annotations

from app.celery_app import celery_app
from app.core.logging import get_logger

logger = get_logger("tasks.cleanup")


@celery_app.task(bind=True, name="app.tasks.cleanup.cleanup_old_audit_logs")
def cleanup_old_audit_logs(self, days: int = 365) -> dict:
    """Archive old audit logs.

    Soft-deletes audit logs older than the specified number of days
    to comply with data retention policies.

    Args:
        days: Number of days to retain logs

    Returns:
        Result dictionary with count of archived logs
    """
    logger.info("audit_cleanup_started", task_id=self.request.id, days=days)

    try:
        import asyncio

        from app.database import async_session_factory
        from app.repositories.audit_repository import AuditLogRepository

        async def _run() -> int:
            async with async_session_factory() as session:
                repository = AuditLogRepository(session)
                count = await repository.cleanup_old_logs(days=days)
                await session.commit()
                return count

        count = asyncio.run(_run())

        logger.info("audit_cleanup_completed", task_id=self.request.id, count=count)

        return {
            "status": "completed",
            "archived_count": count,
            "task_id": self.request.id,
        }

    except Exception as e:
        logger.error("audit_cleanup_failed", task_id=self.request.id, error=str(e))
        raise


@celery_app.task(bind=True, name="app.tasks.cleanup.cleanup_expired_api_keys")
def cleanup_expired_api_keys(self) -> dict:
    """Deactivate API keys that have passed their expiration date.

    Returns:
        Result dictionary with count of deactivated keys
    """
    logger.info("apikey_cleanup_started", task_id=self.request.id)

    try:
        import asyncio

        from app.database import async_session_factory
        from app.repositories.api_key_repository import APIKeyRepository

        async def _run() -> int:
            async with async_session_factory() as session:
                repository = APIKeyRepository(session)
                count = await repository.deactivate_expired_keys()
                await session.commit()
                return count

        count = asyncio.run(_run())

        logger.info("apikey_cleanup_completed", task_id=self.request.id, count=count)

        return {
            "status": "completed",
            "deactivated_count": count,
            "task_id": self.request.id,
        }

    except Exception as e:
        logger.error("apikey_cleanup_failed", task_id=self.request.id, error=str(e))
        raise


@celery_app.task(bind=True, name="app.tasks.cleanup.purge_graveyard")
def purge_graveyard(self, retention_days: int = 30) -> dict:
    """Purge old graveyard entries.

    Args:
        retention_days: Number of days to retain entries

    Returns:
        Result dictionary with count of purged entries
    """
    logger.info(
        "graveyard_purge_started",
        task_id=self.request.id,
        retention_days=retention_days,
    )

    try:
        import asyncio

        from app.database import async_session_factory
        from app.repositories.graveyard_repository import GraveyardRepository

        async def _run() -> int:
            async with async_session_factory() as session:
                repository = GraveyardRepository(session)
                count = await repository.purge(retention_days=retention_days)
                await session.commit()
                return count

        count = asyncio.run(_run())

        logger.info("graveyard_purge_completed", task_id=self.request.id, count=count)

        return {
            "status": "completed",
            "purged_count": count,
            "task_id": self.request.id,
        }

    except Exception as e:
        logger.error("graveyard_purge_failed", task_id=self.request.id, error=str(e))
        raise


@celery_app.task(bind=True, name="app.tasks.cleanup.generate_reports")
def generate_reports(self) -> dict:
    """Generate periodic usage reports.

    Runs daily and logs counts of users, files, and audit log entries
    created in the last 24 hours.

    Returns:
        Result dictionary with the report counts
    """
    logger.info("report_generation_started", task_id=self.request.id)

    try:
        import asyncio
        from datetime import UTC
        from datetime import datetime
        from datetime import timedelta

        from sqlmodel import func
        from sqlmodel import select

        from app.database import async_session_factory
        from app.models.audit_log import AuditLog
        from app.models.file import File
        from app.models.user import User

        async def _run() -> dict[str, int]:
            cutoff = datetime.now(UTC) - timedelta(days=1)
            async with async_session_factory() as session:
                counts: dict[str, int] = {}
                for label, model in (
                    ("new_users", User),
                    ("new_files", File),
                    ("audit_events", AuditLog),
                ):
                    result = await session.execute(
                        select(func.count())
                        .select_from(model)
                        .where(model.created_at >= cutoff)
                    )
                    counts[label] = result.scalar() or 0
                return counts

        counts = asyncio.run(_run())

        logger.info("report_generation_completed", task_id=self.request.id, **counts)

        return {
            "status": "completed",
            "task_id": self.request.id,
            **counts,
        }

    except Exception as e:
        logger.error("report_generation_failed", task_id=self.request.id, error=str(e))
        raise
