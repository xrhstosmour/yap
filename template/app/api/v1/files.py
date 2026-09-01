"""File upload and management API routes.

Provides endpoints for uploading, downloading, and managing files
stored in MinIO / S3-compatible blob storage.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import UploadFile
from fastapi import status

from app.core.logging import get_logger
from app.dependencies import CurrentUser
from app.dependencies import SessionDependency
from app.schemas.files import FileMetadataResponse
from app.schemas.files import FileUploadResponse
from app.schemas.files import FileUrlResponse
from app.services.file_service import FileService
from app.services.file_service import FileServiceError
from app.services.file_service import FileTooLargeError

router = APIRouter(prefix="/files", tags=["Files"])
logger = get_logger("api.files")


@router.post(
    "/upload",
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a file",
    description="Upload a file to blob storage. Supports deduplication via SHA-256.",
)
async def upload_file(
    file: UploadFile,
    current_user: CurrentUser,
    session: SessionDependency,
    is_public: bool = False,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> FileUploadResponse:
    """Upload a file.

    The file content is hashed for deduplication. If an identical file
    already exists, the reference count is incremented instead of
    re-uploading.
    """
    service = FileService(session)
    try:
        record = await service.upload(
            file=file,
            user=current_user,
            is_public=is_public,
            resource_type=resource_type,
            resource_id=resource_id,
        )
    except FileTooLargeError as e:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(e),
        ) from e
    return FileUploadResponse(
        id=record.id,
        filename=record.filename,
        mimetype=record.mimetype,
        size=record.size,
        is_public=record.is_public,
    )


@router.get(
    "/{file_id}/url",
    response_model=FileUrlResponse,
    summary="Get file download URL",
    description="Get a time-limited presigned URL for downloading a file.",
)
async def get_file_url(
    file_id: UUID,
    current_user: CurrentUser,
    session: SessionDependency,
) -> FileUrlResponse:
    """Get a presigned download URL for a file.

    The URL is time-limited (1 hour) and provides direct access to the
    object in blob storage without proxying through the API server.
    """
    service = FileService(session)
    try:
        record = await service.get_owned_file(file_id, current_user)
    except (ValueError, FileServiceError) as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found.",
        ) from e
    url = await service.get_download_url(record)
    thumbnail_url = await service.get_thumbnail_url(record)
    return FileUrlResponse(url=url, thumbnail_url=thumbnail_url)


@router.get(
    "/{file_id}",
    response_model=FileMetadataResponse,
    summary="Get file metadata",
    description="Get metadata for an uploaded file.",
)
async def get_file_metadata(
    file_id: UUID,
    current_user: CurrentUser,
    session: SessionDependency,
) -> FileMetadataResponse:
    """Get file metadata including dimensions, content hash, and timestamps."""
    service = FileService(session)
    try:
        record = await service.get_owned_file(file_id, current_user)
    except (ValueError, FileServiceError) as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found.",
        ) from e
    return FileMetadataResponse(
        id=record.id,
        filename=record.filename,
        mimetype=record.mimetype,
        size=record.size,
        content_hash=record.content_hash,
        is_public=record.is_public,
        image_width=record.image_width,
        image_height=record.image_height,
        resource_type=record.resource_type,
        resource_id=record.resource_id,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
    )


@router.delete(
    "/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a file",
    description=(
        "Soft-delete a file. Purges from storage when reference count reaches zero."
    ),
)
async def delete_file(
    file_id: UUID,
    current_user: CurrentUser,
    session: SessionDependency,
) -> None:
    """Delete a file.

    Decrements the reference count. When it reaches zero, the object
    is permanently removed from blob storage.
    """
    service = FileService(session)
    try:
        await service.delete(file_id, current_user)
    except (ValueError, FileServiceError) as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found.",
        ) from e
