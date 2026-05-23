"""API Key schemas for request/response validation.

This module defines Pydantic schemas for API key
management including creation, updates, and responses.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema
from app.schemas.base import PaginatedResponse
from app.schemas.base import PaginationParams

# Available scopes for API keys.
API_KEY_SCOPES = [
    "read",  # Read-only access
    "write",  # Read and write access
    "admin",  # Full admin access
    "users:read",  # Read users
    "users:write",  # Manage users
    "apikeys:read",  # Read API keys
    "apikeys:write",  # Manage API keys
]


class APIKeyBase(BaseSchema):
    """Base API key schema with common fields."""

    name: str = Field(min_length=1, max_length=255, description="Human-readable name")
    description: str | None = Field(
        default=None, max_length=500, description="Description"
    )
    scopes: list[str] = Field(default_factory=list, description="Permission scopes")


class APIKeyCreate(APIKeyBase):
    """Schema for creating a new API key."""

    expires_in_days: int | None = Field(
        default=None,
        ge=1,
        le=365,
        description="Days until expiration (null for no expiry)",
    )


class APIKeyUpdate(BaseSchema):
    """Schema for updating an API key."""

    name: str | None = Field(
        default=None, min_length=1, max_length=255, description="Name"
    )
    description: str | None = Field(
        default=None, max_length=500, description="Description"
    )
    scopes: list[str] | None = Field(default=None, description="Permission scopes")
    is_active: bool | None = Field(default=None, description="Active status")


class APIKeyResponse(APIKeyBase):
    """Schema for API key response."""

    id: UUID = Field(description="API key ID")
    key_id: str = Field(description="Public key identifier")
    key_prefix: str = Field(description="Key prefix (for identification)")
    is_active: bool = Field(description="Whether key is active")
    last_used_at: datetime | None = Field(description="Last usage timestamp")
    expires_at: datetime | None = Field(description="Expiration timestamp")
    user_id: UUID = Field(description="Owner user ID")
    created_at: datetime = Field(description="Creation timestamp")


class APIKeyCreateResponse(BaseSchema):
    """Response when creating an API key (includes the secret).

    This is the ONLY time the full API key is shown.
    The user must save it immediately as it cannot be recovered.
    """

    id: UUID = Field(description="API key ID")
    key_id: str = Field(description="Public key identifier")
    api_key: str = Field(description="The API key (SAVE THIS NOW - shown only once!)")
    name: str = Field(description="Key name")
    scopes: list[str] = Field(description="Permission scopes")
    expires_at: datetime | None = Field(description="Expiration timestamp")
    created_at: datetime = Field(description="Creation timestamp")


class APIKeyListResponse(PaginatedResponse[APIKeyResponse]):
    """Paginated list of API keys."""

    pass


class APIKeyListParams(PaginationParams):
    """Query parameters for listing API keys."""

    is_active: bool | None = Field(default=None, description="Filter by active status")
    user_id: UUID | None = Field(default=None, description="Filter by user")
    scope: str | None = Field(default=None, description="Filter by scope")
