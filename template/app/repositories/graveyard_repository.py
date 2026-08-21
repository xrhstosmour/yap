"""Graveyard repository for deleted record tracking."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import Any
from typing import cast
from uuid import UUID

from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.logging import get_logger
from app.core.tenant import get_current_tenant_id
from app.core.tenant import is_system_access
from app.models.graveyard import DEFAULT_RETENTION_DAYS
from app.models.graveyard import Graveyard
from app.repositories.base import TenantContextRequiredError

logger = get_logger("repository.graveyard")


class GraveyardRepository:
    """Repository for Graveyard model operations.

    Standalone repository that does not inherit from BaseRepository
    because graveyard rows should never be tenant-filtered or soft-deleted.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def bury(
        self,
        model_name: str,
        record_id: UUID,
        data: dict[str, Any],
        deleted_by: str = "system",
        reason: str | None = None,
        tenant_id: UUID | None = None,
    ) -> Graveyard:
        """Create a graveyard entry for a deleted record.

        Args:
            model_name: The SQLModel table name.
            record_id: UUID of the deleted record.
            data: Full snapshot of the record at deletion time.
            deleted_by: Who performed the deletion.
            reason: Optional reason.
            tenant_id: Tenant context.

        Returns:
            Created Graveyard entry.
        """
        entry = Graveyard(
            model_name=model_name,
            record_id=record_id,
            data=data,
            deleted_by=str(deleted_by),
            record_deleted_at=datetime.now(UTC),
            reason=reason,
            tenant_id=tenant_id,
        )
        self.session.add(entry)
        await self.session.flush()
        await self.session.refresh(entry)
        return entry

    async def purge(
        self,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> int:
        """Purge graveyard entries older than retention period.

        Args:
            retention_days: Days to retain entries.

        Returns:
            Number of entries purged.
        """
        from sqlalchemy import delete as sa_delete

        cutoff = datetime.now(UTC) - timedelta(days=retention_days)

        statement = sa_delete(Graveyard).where(
            Graveyard.record_deleted_at < cutoff  # type: ignore[arg-type]
        )
        result = cast(CursorResult[Any], await self.session.execute(statement))
        await self.session.flush()

        row_count = result.rowcount or 0

        logger.info("graveyard_purged", count=row_count, retention_days=retention_days)
        return row_count

    async def recover(self, record_id: UUID) -> dict[str, Any] | None:
        """Retrieve a graveyard entry for potential recovery.

        Scoped to the active tenant: a graveyard snapshot holds every
        column of the original row, including PII and secret hashes, so
        an unscoped lookup would let one tenant recover another tenant's
        deleted record by guessing its `record_id`. Unlike `bury()` and
        `purge()`, which this class deliberately keeps free of
        `BaseRepository`'s ambient-tenant machinery so background jobs can
        write or sweep across every tenant, `recover()` is the one
        exception: a real lookup by a caller who should only ever see
        their own tenant's data, so it fails closed the same way
        `BaseRepository._apply_tenant_filter()` does rather than silently
        running unfiltered when no tenant context is set.

        Args:
            record_id: UUID of the deleted record.

        Returns:
            Record data dict or None.

        Raises:
            TenantContextRequiredError: If no tenant context is set and
                `system_context()` is not active.
        """
        tenant_id = get_current_tenant_id()
        query = (
            select(Graveyard)
            .where(Graveyard.record_id == record_id)  # type: ignore[arg-type]
            .order_by(Graveyard.record_deleted_at.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        if tenant_id is not None:
            query = query.where(Graveyard.tenant_id == tenant_id)  # type: ignore[arg-type]
        elif not is_system_access():
            message = (
                "Graveyard.recover() requires a tenant context. Set one "
                "with tenant_context(...), or wrap deliberate cross-tenant "
                "access in system_context()."
            )
            raise TenantContextRequiredError(message)
        result = await self.session.execute(query)
        entry = result.scalar_one_or_none()
        return entry.data if entry else None
