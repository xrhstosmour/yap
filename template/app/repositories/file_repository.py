"""File repository for blob storage metadata operations."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.file import File
from app.repositories.base import BaseRepository

logger = get_logger("repository.file")


class FileRepository(BaseRepository[File]):
    """Repository for File model operations."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, File)

    async def get_by_content_hash(self, content_hash: str) -> File | None:
        """Find a file record by its content hash (for dedup)."""
        query = select(File).where(
            File.content_hash == content_hash,  # type: ignore[arg-type]
            File.deleted_at.is_(None),  # type: ignore[union-attr]
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

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
        """Increment the reference count for a content hash."""
        from sqlalchemy import update

        await self.session.execute(
            update(File)
            .where(File.content_hash == content_hash)  # type: ignore[arg-type]
            .values(reference_count=File.reference_count + 1)
        )
        await self.session.flush()

    async def decrement_reference_count(self, file_id: UUID) -> int:
        """Decrement reference count and return the new count."""
        from sqlalchemy import update

        record = await self.get(file_id)
        if not record:
            return 0
        new_count = max(0, record.reference_count - 1)
        await self.session.execute(
            update(File)
            .where(File.id == file_id)  # type: ignore[arg-type]
            .values(reference_count=new_count)
        )
        await self.session.flush()
        return new_count
