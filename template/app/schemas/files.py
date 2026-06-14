"""File schemas for blob storage endpoints."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema


class FileUploadResponse(BaseSchema):
    """Response after a successful file upload.

    Attributes:
        id: File UUID.
        filename: Original filename.
        mimetype: MIME type.
        size: File size in bytes.
        is_public: Whether the file is publicly accessible.
    """

    id: UUID = Field(description="File UUID")
    filename: str = Field(description="Original filename")
    mimetype: str = Field(description="MIME type")
    size: int = Field(description="File size in bytes")
    is_public: bool = Field(description="Whether the file is publicly accessible")


class FileUrlResponse(BaseSchema):
    """Response containing a presigned URL for file access.

    Attributes:
        url: Presigned URL (time-limited) for downloading the file.
        thumbnail_url: Presigned URL for the thumbnail (images only).
    """

    url: str = Field(description="Presigned download URL")
    thumbnail_url: str | None = Field(
        default=None, description="Presigned thumbnail URL (images only)"
    )


class FileMetadataResponse(BaseSchema):
    """Full metadata for a stored file.

    Attributes:
        id: File UUID.
        filename: Original filename.
        mimetype: MIME type.
        size: File size in bytes.
        content_hash: SHA-256 hash of the file content.
        is_public: Whether the file is publicly accessible.
        image_width: Image width in pixels (if image).
        image_height: Image height in pixels (if image).
        resource_type: Optional resource type this file is attached to.
        resource_id: Optional resource ID this file is attached to.
        created_at: When the file was uploaded.
        updated_at: When the file was last modified.
    """

    id: UUID = Field(description="File UUID")
    filename: str = Field(description="Original filename")
    mimetype: str = Field(description="MIME type")
    size: int = Field(description="File size in bytes")
    content_hash: str = Field(description="SHA-256 content hash")
    is_public: bool = Field(description="Whether the file is publicly accessible")
    image_width: int | None = Field(
        default=None, description="Image width in pixels"
    )
    image_height: int | None = Field(
        default=None, description="Image height in pixels"
    )
    resource_type: str | None = Field(
        default=None, description="Resource type this file is attached to"
    )
    resource_id: str | None = Field(
        default=None, description="Resource ID this file is attached to"
    )
    created_at: str = Field(description="Upload timestamp")
    updated_at: str = Field(description="Last modified timestamp")
