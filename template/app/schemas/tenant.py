"""Tenant schemas for request/response validation."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema
from app.schemas.base import PaginatedResponse
from app.schemas.base import PaginationParameters


class TenantCreate(BaseSchema):
    """Schema for creating a new tenant."""

    name: str = Field(min_length=1, max_length=255, description="Display name")
    slug: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$",
        description="URL-safe identifier",
    )
    settings: dict[str, Any] | None = Field(default=None, description="Tenant settings")


class TenantUpdate(BaseSchema):
    """Schema for updating a tenant."""

    name: str | None = Field(
        default=None, min_length=1, max_length=255, description="Display name"
    )
    slug: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$",
        description="URL-safe identifier",
    )
    is_active: bool | None = Field(default=None, description="Active status")
    settings: dict[str, Any] | None = Field(default=None, description="Tenant settings")


class TenantResponse(BaseSchema):
    """Schema for tenant response."""

    id: UUID = Field(description="Tenant ID")
    name: str = Field(description="Display name")
    slug: str = Field(description="URL-safe identifier")
    is_active: bool = Field(description="Whether tenant is active")
    settings: dict[str, Any] = Field(description="Tenant settings")
    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime = Field(description="Last update timestamp")


class TenantListParameters(PaginationParameters):
    """Query parameters for listing tenants."""

    is_active: bool | None = Field(default=None, description="Filter by active status")
    search: str | None = Field(default=None, description="Search in name/slug")
    # Narrowed from the inherited free-form `str`. Any attribute name used to
    # reach `getattr(Tenant, sort_by)`, and `metadata` resolves on every
    # SQLModel class, so `?sort_by=metadata` handed SQLAlchemy's `MetaData`
    # object to `order_by` and returned a 500.
    sort_by: Literal["name", "slug", "is_active", "created_at", "updated_at"] | None = (
        Field(default=None, description="Column to sort by")
    )


class TenantListResponse(PaginatedResponse[TenantResponse]):
    """Paginated list of tenants."""

    pass
