"""File repository for blob storage metadata operations."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import Any
from uuid import UUID
from uuid import uuid7

from sqlalchemy import case
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
        self,
        content_hash: str,
        uploaded_by: UUID,
        tenant_id: UUID | None = None,
    ) -> File | None:
        """Find a file record by its content hash, scoped to one uploader.

        Deduplication is per-uploader (see the ``File`` docstring), so a
        content-hash lookup scoped no tighter than the tenant would match a
        colleague's identical upload and hand back a row this caller does
        not own: their filename, their visibility, their file ID, and a
        reference this caller can never release, since ``get_owned``
        filters on ``uploaded_by``.

        Args:
            content_hash: SHA-256 hash to look up.
            uploaded_by: Owner to scope the lookup to.
            tenant_id: Tenant to scope the lookup to. Defaults to the
                current tenant context.

        Returns:
            The matching file record, or ``None``.
        """
        if tenant_id is None:
            tenant_id = get_current_tenant_id()
        query = select(File).where(
            File.content_hash == content_hash,  # type: ignore[arg-type]
            File.uploaded_by == uploaded_by,  # type: ignore[arg-type]
            File.tenant_id == tenant_id,  # type: ignore[arg-type]
            File.deleted_at.is_(None),  # type: ignore[union-attr]
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create_or_increment(self, data: dict[str, Any]) -> tuple[File, bool]:
        """Insert a new file record, or increment ``reference_count`` if one
        with the same ``(tenant_id, uploaded_by, content_hash)`` already exists.

        Uses ``INSERT ... ON CONFLICT (tenant_id, uploaded_by, content_hash)
        DO UPDATE`` so the database resolves duplicate-content races
        atomically: two concurrent uploads of identical content, by the same
        user, can no longer both pass a stale existence check and create
        duplicate rows.

        A soft-deleted row still wins that conflict, the unique constraint
        knows nothing about ``deleted_at``, so re-uploading content that was
        once deleted lands on the retired row rather than inserting. It is
        resurrected here, adopting the incoming upload wholesale.

        Args:
            data: Field values for the new file record.

        Returns:
            Tuple of ``(record, created)``. ``created`` is ``True`` when
            this upload owns the row's content, a fresh insert or a
            resurrection, and ``False`` when it merely took another
            reference on content already stored.
        """
        tenant_id = get_current_tenant_id()
        if tenant_id and "tenant_id" not in data:
            data["tenant_id"] = tenant_id

        now = datetime.now(UTC)
        data.setdefault("created_at", now)
        data.setdefault("updated_at", now)
        new_id = data.setdefault("id", uuid7())

        statement = pg_insert(File).values(**data)
        excluded = statement.excluded
        was_deleted = File.deleted_at.is_not(None)  # type: ignore[union-attr]

        # Resurrection takes the incoming upload's values for everything
        # that describes the content. Keeping the retired row's would hand
        # the uploader a `thumbnail_object_key` pointing at an object the
        # delete already purged. `uploaded_by` is not in the list: it is
        # part of the conflict target now, so a conflicting row already
        # carries the same value.
        adopted: dict[str, Any] = {
            column: case((was_deleted, excluded[column]), else_=getattr(File, column))
            for column in (
                "filename",
                "mimetype",
                "size",
                "bucket",
                "object_key",
                "thumbnail_object_key",
                "image_width",
                "image_height",
                "is_public",
                "resource_type",
                "resource_id",
                "created_at",
            )
        }

        statement = statement.on_conflict_do_update(
            index_elements=["tenant_id", "uploaded_by", "content_hash"],
            set_={
                **adopted,
                "deleted_at": None,
                # A retired row's count was driven to zero and its blob
                # purged before the delete, so this upload is the first
                # reference again, not the next one.
                "reference_count": case(
                    (was_deleted, 1),
                    else_=File.reference_count + 1,
                ),
                "updated_at": now,
            },
        ).returning(File.id, File.reference_count)  # type: ignore[call-overload]

        result = await self.session.execute(statement)
        row_id, row_reference_count = result.one()
        await self.session.flush()

        # A resurrected row is indistinguishable from a fresh insert by id
        # alone, so the count decides: the statement above resets it to one
        # in exactly those two cases. A live row somehow sitting at zero
        # references would read the same and be treated as created, which is
        # what it effectively is, its blob was re-uploaded a moment ago.
        created = row_id == new_id or row_reference_count == 1

        # Fetched by the id the statement just returned, not re-queried by
        # content hash: the row is guaranteed to exist and cannot be
        # filtered back out. `populate_existing` refreshes any stale
        # identity-map copy with the values the upsert wrote server-side.
        result = await self.session.execute(
            select(File)
            .where(File.id == row_id)  # type: ignore[arg-type]
            .execution_options(populate_existing=True)
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise RuntimeError(
                "create_or_increment: no row found for the id returned by "
                f"the upsert ({row_id})"
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

    async def increment_reference_count(
        self, content_hash: str, uploaded_by: UUID
    ) -> None:
        """Increment the reference count for one uploader's content hash,
        within the current tenant.

        Deduplication is per-uploader (see the ``File`` docstring): scoped
        any wider, this would match and increment rows sharing this
        ``content_hash`` that belong to other users, or other tenants,
        rather than only the caller's own.

        Args:
            content_hash: SHA-256 hash of the content.
            uploaded_by: Owner of the row to increment.
        """
        from sqlalchemy import update

        tenant_id = get_current_tenant_id()
        await self.session.execute(
            update(File)
            .where(
                File.content_hash == content_hash,  # type: ignore[arg-type]
                File.uploaded_by == uploaded_by,  # type: ignore[arg-type]
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
