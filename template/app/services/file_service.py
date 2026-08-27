"""File service for upload, download, and management of blob storage files."""

from __future__ import annotations

import hashlib
import os
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.settings import settings
from app.core.storage import delete_object
from app.core.storage import get_download_url
from app.core.storage import is_thumbnailable
from app.core.storage import upload_file
from app.models.file import File
from app.models.user import User
from app.repositories.file_repository import FileRepository

logger = get_logger("service.file")

MAX_UPLOAD_SIZE = 25 * 1024 * 1024  # 25MB


class FileServiceError(Exception):
    """Base exception for file service operations."""


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
        # Sanitize filename to prevent path traversal.
        safe_filename = os.path.basename(file.filename or "untitled")
        safe_filename = safe_filename.replace("\x00", "")

        mimetype = file.content_type or "application/octet-stream"

        # Stream the file in chunks to limit memory usage and compute hash.
        # A bytearray avoids the O(n^2) cost of repeated bytes concatenation
        # (each `content += chunk` on bytes reallocates and copies the whole
        # buffer; bytearray.extend() amortizes to O(n) overall).
        buffer = bytearray()
        hash_sha256 = hashlib.sha256()
        while True:
            chunk = await file.read(8192)
            if not chunk:
                break
            buffer.extend(chunk)
            hash_sha256.update(chunk)
            if len(buffer) > MAX_UPLOAD_SIZE:
                raise FileServiceError(
                    f"File exceeds maximum size of {MAX_UPLOAD_SIZE} bytes"
                )
        content = bytes(buffer)
        content_hash = hash_sha256.hexdigest()

        # Fast-path dedup check to skip the storage upload for the common
        # case. This alone is racy (two concurrent uploads of identical
        # content can both miss each other's in-flight row), so the actual
        # insert below is made race-safe via a DB-level unique constraint
        # on content_hash plus an atomic upsert.
        existing = await self.file_repository.get_by_content_hash(content_hash)
        if existing:
            await self.file_repository.increment_reference_count(content_hash)
            logger.info(
                "file_upload_dedup",
                content_hash=content_hash[:16],
                reference_count=existing.reference_count + 1,
            )
            return existing

        # Upload to blob storage. Thumbnails are generated afterward by a
        # Celery task, not inline, so this doesn't hold the request open.
        object_key, _ = await upload_file(content=content, mimetype=mimetype)

        record, created = await self.file_repository.create_or_increment(
            {
                "filename": safe_filename,
                "mimetype": mimetype,
                "size": len(content),
                "content_hash": content_hash,
                "bucket": settings.STORAGE_BUCKET,
                "object_key": object_key,
                "thumbnail_object_key": None,
                "image_width": None,
                "image_height": None,
                "is_public": is_public,
                "reference_count": 1,
                "uploaded_by": user.id,
                "resource_type": resource_type,
                "resource_id": resource_id,
            }
        )

        if not created:
            # Lost the race: another upload of identical content committed
            # first. Nothing to clean up, and nothing may be cleaned up.
            # Object keys are content-addressed and tenant-namespaced (see
            # `build_object_key`), so the winner's row points at the exact
            # key this upload just wrote, with the exact same bytes.
            # Deleting it here purged the blob out from under the winner and
            # every later referencer, leaving rows whose downloads 404.
            logger.info(
                "file_upload_dedup_race",
                content_hash=content_hash[:16],
                reference_count=record.reference_count,
            )
            return record

        if is_thumbnailable(mimetype):
            from app.tasks.storage import generate_thumbnail_task

            try:
                generate_thumbnail_task.delay(file_id=str(record.id))
            except Exception:
                logger.warning("thumbnail_task_dispatch_failed", file_id=str(record.id))

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
            FileServiceError: If the file is not found or not owned by the user.
        """
        record = await self.file_repository.get_owned(file_id, user.id)
        if not record:
            raise FileServiceError("File not found.")
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
            FileServiceError: If the file is not found or not owned.
        """
        record = await self.get_owned_file(file_id, user)
        new_count = await self.file_repository.decrement_reference_count(file_id)

        # One row serves every reference to the same content within a tenant
        # (see `create_or_increment`), so dropping a single reference must
        # not retire the row while others still hold it. Soft-deleting here
        # unconditionally made the row invisible to `get_owned`, so every
        # remaining referencer got "File not found." on a file they still
        # owned, with `reference_count` left stranded above zero.
        if new_count == 0:
            # Delete thumbnail FIRST to avoid orphaned thumbnails if main
            # delete succeeds but thumbnail delete fails.
            if record.thumbnail_object_key:
                await delete_object(
                    object_key=record.thumbnail_object_key,
                    bucket=record.bucket,
                )
            await delete_object(object_key=record.object_key, bucket=record.bucket)
            await self.file_repository.delete(file_id)

        logger.info(
            "file_deleted",
            file_id=str(file_id),
            reference_count=new_count,
        )
