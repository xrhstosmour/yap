"""File model for blob storage (MinIO / S3-compatible).

Stores metadata for uploaded files. The actual binary data lives in
the storage backend; this table tracks ownership, deduplication via
content hash, and reference counting for safe garbage collection.
"""

from __future__ import annotations

from uuid import UUID

from sqlmodel import Field

from app.models.base import BaseModel


class File(BaseModel, table=True):
    """Uploaded file stored in MinIO / S3-compatible blob storage.

    Each row represents one logical file. Deduplication is handled by
    ``content_hash`` (SHA-256): when multiple records share the same
    hash, ``reference_count`` is incremented instead of uploading
    duplicates. The storage backend object is deleted when
    ``reference_count`` reaches zero.

    Inherits ``tenant_id`` from ``BaseModel`` like every other table, but it
    is not a reliable ownership signal here: content is deduplicated
    globally across tenants (see ``content_hash``), so a shared row's
    ``tenant_id`` is whichever tenant uploaded that content first, not every
    tenant that references it. ``BaseRepository._apply_tenant_filter`` does
    apply its usual ``WHERE tenant_id = ...`` filter for this model, it just
    filters on a column that does not mean "who can access this row" the way
    it does elsewhere. Ownership is ``uploaded_by``, not tenant membership.
    Never call ``FileRepository.get()`` directly for a caller-facing read or
    write; use ``get_owned()`` (or an explicit ``resource_id``/
    ``resource_type`` ownership check, as the public business-media
    endpoints do) so one tenant's private files can't be reached by ID from
    another tenant, and so a second tenant referencing shared content isn't
    incorrectly filtered out by the first tenant's ``tenant_id``.
    """

    __tablename__ = "files"  # pyright: ignore[reportAssignmentType]

    filename: str = Field(
        nullable=False,
        max_length=512,
    )

    mimetype: str = Field(
        nullable=False,
        max_length=255,
    )

    size: int = Field(
        nullable=False,
    )

    # Globally unique (not tenant-scoped): identical content is stored once
    # regardless of tenant, mirroring User.email and other codebase-wide
    # unique columns. The constraint also closes the race between
    # concurrent uploads of identical content on the dedup check.
    content_hash: str = Field(
        nullable=False,
        max_length=64,
        unique=True,
        index=True,
    )

    bucket: str = Field(
        nullable=False,
        max_length=255,
    )

    object_key: str = Field(
        nullable=False,
        max_length=1024,
    )

    thumbnail_object_key: str | None = Field(
        default=None,
        max_length=1024,
    )

    image_width: int | None = Field(
        default=None,
        nullable=True,
    )

    image_height: int | None = Field(
        default=None,
        nullable=True,
    )

    is_public: bool = Field(
        default=False,
        nullable=False,
    )

    reference_count: int = Field(
        default=1,
        nullable=False,
    )

    uploaded_by: UUID = Field(
        nullable=False,
        index=True,
        foreign_key="users.id",
    )

    resource_type: str | None = Field(
        default=None,
        max_length=100,
    )

    resource_id: str | None = Field(
        default=None,
        max_length=100,
    )
