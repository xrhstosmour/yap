"""File service for upload, download, and management of blob storage files."""

from __future__ import annotations

from uuid import UUID

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.storage import delete_object
from app.core.storage import get_download_url
from app.core.storage import upload_file
from app.models.file import File
from app.models.user import User
from app.repositories.file_repository import FileRepository

logger = get_logger("service.file")


class FileService:
    """Service for file upload, download, and lifecycle management."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.file_repository = FileRepository(session)

    async def upload(
        self,
        file: UploadFile,
        user: User,
        is_public: bool = False,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> File:
        """Upload a file with deduplication.

        Computes the content hash and checks for an existing record.
        If found, increments the reference count. Otherwise uploads
        to blob storage and creates a new record.

        Args:
            file: The uploaded file from the request.
            user: The authenticated user uploading the file.
            is_public: Whether the file is publicly accessible.
            resource_type: Optional resource type to associate.
            resource_id: Optional resource ID to associate.

        Returns:
            The created ``File`` record.
        """
        content = await file.read()
        mimetype = file.content_type or "application/octet-stream"

        # Dedup check.
        content_hash = hashlib_content(content)
        existing = await self.file_repository.get_by_content_hash(content_hash)
        if existing:
            await self.file_repository.increment_reference_count(content_hash)
            logger.info(
                "file_upload_dedup",
                content_hash=content_hash[:16],
                reference_count=existing.reference_count + 1,
            )
            return existing

        # Upload to blob storage.
        object_key, _, image_width, image_height, thumbnail_key = await upload_file(
            content=content,
            filename=file.filename or "untitled",
            mimetype=mimetype,
        )

        record = File(
            filename=file.filename or "untitled",
            mimetype=mimetype,
            size=len(content),
            content_hash=content_hash,
            bucket="default",  # Managed via settings.STORAGE_BUCKET in core.
            object_key=object_key,
            thumbnail_object_key=thumbnail_key,
            image_width=image_width,
            image_height=image_height,
            is_public=is_public,
            reference_count=1,
            uploaded_by=user.id,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        self.session.add(record)
        await self.session.flush()

        logger.info(
            "file_uploaded",
            file_id=str(record.id),
            filename=record.filename,
            size=record.size,
        )
        return record

    async def get_owned_file(self, file_id: UUID, user: User) -> File:
        """Get a file owned by the current user.

        Raises:
            ValueError: If the file is not found or not owned by the user.
        """
        record = await self.file_repository.get_owned(file_id, user.id)
        if not record:
            raise ValueError("File not found.")
        return record

    async def get_download_url(self, record: File) -> str:
        """Generate a presigned download URL for a file."""
        return await get_download_url(
            object_key=record.object_key,
            bucket=record.bucket,
        )

    async def get_thumbnail_url(self, record: File) -> str | None:
        """Generate a presigned download URL for the thumbnail, if available."""
        if not record.thumbnail_object_key:
            return None
        return await get_download_url(
            object_key=record.thumbnail_object_key,
            bucket=record.bucket,
        )

    async def delete(self, file_id: UUID, user: User) -> None:
        """Soft-delete a file. Purges from storage when reference count reaches zero.

        Args:
            file_id: UUID of the file to delete.
            user: The authenticated user requesting deletion.

        Raises:
            ValueError: If the file is not found or not owned.
        """
        record = await self.get_owned_file(file_id, user)
        new_count = await self.file_repository.decrement_reference_count(file_id)

        if new_count == 0:
            await delete_object(object_key=record.object_key, bucket=record.bucket)
            if record.thumbnail_object_key:
                await delete_object(
                    object_key=record.thumbnail_object_key,
                    bucket=record.bucket,
                )

        await self.file_repository.delete(file_id)

        logger.info(
            "file_deleted",
            file_id=str(file_id),
            reference_count=new_count,
        )


def hashlib_content(content: bytes) -> str:
    """Compute SHA-256 hex digest of file content."""
    import hashlib

    return hashlib.sha256(content).hexdigest()
