"""File model for blob storage (MinIO / S3-compatible).

Stores metadata for uploaded files. The actual binary data lives in
the storage backend; this table tracks ownership, deduplication via
content hash, and reference counting for safe garbage collection.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import UniqueConstraint
from sqlmodel import Field

from app.models.base import BaseModel


class File(BaseModel, table=True):
    """Uploaded file stored in MinIO / S3-compatible blob storage.

    Each row represents one logical file. Deduplication is handled by
    ``content_hash`` (SHA-256): when multiple records within the same
    tenant share a hash, ``reference_count`` is incremented instead of
    uploading duplicates. The storage backend object is deleted when
    ``reference_count`` reaches zero.

    Deduplication is scoped per tenant (``UNIQUE(tenant_id, content_hash)``),
    not global. Two tenants uploading identical content each get their own
    row, own ``uploaded_by``, and own storage object, at the cost of storing
    that content twice instead of once. The alternative (a single row shared
    across tenants) would need a many-to-many ownership record, since
    ``uploaded_by`` is a single FK: a second tenant referencing an
    already-shared row would either never see it (filtered out by the first
    tenant's ``tenant_id``) or silently inherit the first tenant's metadata,
    ownership and access.

    A row with no ``tenant_id`` (``NULL``) still dedupes against other
    ``NULL``-tenant rows with the same content: the constraint below is
    declared ``NULLS NOT DISTINCT`` (Postgres 15+), overriding Postgres's
    default where two ``NULL`` values never count as a conflict for a
    unique constraint. Without it, two concurrent uploads with no tenant
    context would both insert instead of racing safely onto one row, the
    exact bug this migration exists to prevent, just triggered by a missing
    tenant instead of a mismatched one.
    """

    __tablename__ = "files"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "content_hash",
            name="uq_files_tenant_id_content_hash",
            postgresql_nulls_not_distinct=True,
        ),
    )

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

    # Unique per tenant, not globally: see the class docstring. The
    # `(tenant_id, content_hash)` constraint on `__table_args__` is what
    # enforces this and provides the lookup index; a standalone index here
    # would be redundant with it. The constraint also closes the race
    # between concurrent uploads of identical content on the dedup check.
    content_hash: str = Field(
        nullable=False,
        max_length=64,
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
