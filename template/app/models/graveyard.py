"""Graveyard model for tracking deleted records.

Provides a centralized audit trail of all soft-deleted records across
all models. Enables GDPR right-to-access, data recovery, and compliance.

The graveyard stores a JSON snapshot of the record at the time of deletion,
allowing full reconstruction if needed.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON
from sqlmodel import Field

from app.models.base import BaseModel

DEFAULT_RETENTION_DAYS = 30


class Graveyard(BaseModel, table=True):
    """Tombstone record for soft-deleted data.

    Every soft delete in the system creates a graveyard entry containing
    a full snapshot of the deleted record. Entries are automatically
    purged after the retention period.

    Attributes:
        id: UUID primary key
        model_name: The SQLModel table name (e.g. "users", "api_keys")
        record_id: UUID of the deleted record
        data: Full JSON snapshot of the record at deletion time
        deleted_by: UUID of the user or "system"
        record_deleted_at: When the deletion occurred
        reason: Optional reason for deletion
        tenant_id: Multi-tenant isolation
        created_at: When the graveyard entry was created
    """

    __tablename__ = "graveyard"  # pyright: ignore[reportAssignmentType]

    model_name: str = Field(
        nullable=False,
        index=True,
        max_length=100,
    )

    record_id: UUID = Field(
        nullable=False,
        index=True,
    )

    data: dict[str, Any] = Field(
        default_factory=dict,
        sa_type=JSON,
        nullable=False,
    )

    deleted_by: str = Field(
        default="system",
        max_length=100,
    )

    record_deleted_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )

    reason: str | None = Field(
        default=None,
        max_length=500,
    )

    tenant_id: UUID = Field(
        nullable=False,
        index=True,
        foreign_key="tenants.id",
    )
