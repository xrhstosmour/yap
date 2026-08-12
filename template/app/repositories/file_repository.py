"""File repository for blob storage metadata operations."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import Any
from uuid import UUID
from uuid import uuid7

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.tenant import get_current_tenant_id
from app.models.file import File
from app.repositories.base import BaseRepository

logger = get_logger("repository.file")


class FileRepository(BaseRepository[File]):
    """Repository for File model operations."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, File)

    async def get_by_content_hash(
        self, content_hash: str, tenant_id: UUID | None = None
    ) -> File | None:
        """Find a file record by its content hash, scoped to a tenant.

        Deduplication is per-tenant (see the ``File`` docstring), so a
        content-hash lookup without a tenant would either miss another
        tenant's identical upload or, worse, match it and hand back a row
        this caller does not own.

        Args:
            content_hash: SHA-256 hash to look up.
            tenant_id: Tenant to scope the lookup to. Defaults to the
                current tenant context.

        Returns:
            The matching file record, or ``None``.
        """
        if tenant_id is None:
            tenant_id = get_current_tenant_id()
        query = select(File).where(
            File.content_hash == content_hash,  # type: ignore[arg-type]
            File.tenant_id == tenant_id,  # type: ignore[arg-type]
            File.deleted_at.is_(None),  # type: ignore[union-attr]
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create_or_increment(self, data: dict[str, Any]) -> tuple[File, bool]:
        """Insert a new file record, or increment ``reference_count`` if one
        with the same ``(tenant_id, content_hash)`` already exists.

        Uses ``INSERT ... ON CONFLICT (tenant_id, content_hash) DO UPDATE``
        so the database resolves duplicate-content races atomically: two
        concurrent uploads of identical content, by the same tenant, can no
        longer both pass a stale existence check and create duplicate rows.

        Args:
            data: Field values for the new file record.

        Returns:
            Tuple of ``(record, created)``. ``created`` is ``True`` when a
            new row was inserted, ``False`` when an existing row's
            ``reference_count`` was incremented instead.
        """
        tenant_id = get_current_tenant_id()
        if tenant_id and "tenant_id" not in data:
            data["tenant_id"] = tenant_id

        now = datetime.now(UTC)
        data.setdefault("created_at", now)
        data.setdefault("updated_at", now)
        new_id = data.setdefault("id", uuid7())

        statement = (
            pg_insert(File)
            .values(**data)
            .on_conflict_do_update(
                index_elements=["tenant_id", "content_hash"],
                set_={
                    "reference_count": File.reference_count + 1,
                    "updated_at": now,
                },
            )
            .returning(File.id)  # type: ignore[call-overload]
        )
        result = await self.session.execute(statement)
        row_id = result.scalar_one()
        await self.session.flush()

        created = row_id == new_id
        record = await self.get_by_content_hash(
            data["content_hash"], tenant_id=data.get("tenant_id")
        )
        if record is None:
            raise RuntimeError(
                "create_or_increment: no row found for content_hash "
                f"immediately after upsert ({data['content_hash'][:16]}...)"
            )
        return record, created

    async def get_owned(self, file_id: UUID, user_id: UUID) -> File | None:
        """Get a file by ID ensuring the requesting user owns it."""
        query = select(File).where(
            File.id == file_id,  # type: ignore[arg-type]
            File.uploaded_by == user_id,  # type: ignore[arg-type]
            File.deleted_at.is_(None),  # type: ignore[union-attr]
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def increment_reference_count(self, content_hash: str) -> None:
        """Increment the reference count for a content hash, within the
        current tenant.

        Deduplication is per-tenant (see the ``File`` docstring): without
        the tenant filter, this would match and increment every tenant's
        row sharing this ``content_hash``, not just the caller's own.
        """
        from sqlalchemy import update

        tenant_id = get_current_tenant_id()
        await self.session.execute(
            update(File)
            .where(
                File.content_hash == content_hash,  # type: ignore[arg-type]
                File.tenant_id == tenant_id,  # type: ignore[arg-type]
            )
            .values(reference_count=File.reference_count + 1)
        )
        await self.session.flush()

    async def decrement_reference_count(self, file_id: UUID) -> int:
        """Decrement reference count and return the new count.

        A single atomic ``UPDATE ... RETURNING`` rather than a read in
        Python followed by a write: two concurrent deletes of files sharing
        a ``content_hash`` (see ``create_or_increment``) could otherwise
        both read the same starting count and one decrement would be lost,
        leaving the count too high, or a caller purging the shared blob
        too early. Mirrors the already-atomic ``increment_reference_count``.
        """
        from sqlalchemy import func
        from sqlalchemy import update

        statement = (
            update(File)
            .where(File.id == file_id)  # type: ignore[arg-type]
            .values(reference_count=func.greatest(File.reference_count - 1, 0))
            .returning(File.reference_count)  # type: ignore[call-overload]
        )
        statement = self._apply_tenant_filter(statement)
        statement = self._apply_soft_delete_filter(statement)

        result = await self.session.execute(statement)
        await self.session.flush()
        row = result.first()
        return row[0] if row is not None else 0
