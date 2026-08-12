"""Base model with multi-tenant support and soft delete.

This module provides the base SQLModel class that all models inherit from.
It adds automatic tenant filtering, soft delete support, and common fields.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import Any
from uuid import UUID
from uuid import uuid7

from sqlmodel import Field
from sqlmodel import SQLModel


def get_utc_now() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(UTC)


class BaseModel(SQLModel, table=False):
    """Abstract base model with common fields.

    All database models should inherit from this class. It provides:
    - UUIDv7 primary key (time-sortable, 128-bit)
    - Automatic created_at timestamp
    - Updated_at timestamp that auto-updates
    - Soft delete support with deleted_at
    - Multi-tenant support with tenant_id

    Attributes:
        id: UUIDv7 primary key
        created_at: When the record was created
        updated_at: When the record was last modified
        deleted_at: Soft delete timestamp (null if not deleted)
        tenant_id: Multi-tenant organization ID
    """

    id: UUID = Field(
        default_factory=uuid7,
        primary_key=True,
        nullable=False,
    )

    created_at: datetime = Field(
        default_factory=get_utc_now,
        nullable=False,
        index=True,
    )

    updated_at: datetime = Field(
        default_factory=get_utc_now,
        nullable=False,
    )

    deleted_at: datetime | None = Field(
        default=None,
        nullable=True,
        index=True,
    )

    tenant_id: UUID | None = Field(
        default=None,
        nullable=True,
        index=True,
    )


class TenantBase(SQLModel):
    """Base fields for Tenant model."""

    name: str = Field(min_length=1, max_length=255, nullable=False, index=True)
    slug: str = Field(min_length=1, max_length=100, nullable=False, unique=True)
    is_active: bool = Field(default=True, nullable=False)
    settings: dict[str, Any] = Field(default_factory=dict, nullable=False)
