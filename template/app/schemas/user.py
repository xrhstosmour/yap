"""User schemas for request/response validation.

This module defines Pydantic schemas for user-related
operations including creation, updates, and responses.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import EmailStr
from pydantic import Field

from app.core.phone_number import PhoneNumberString
from app.models.user import UserRole
from app.schemas.base import BaseSchema
from app.schemas.base import PaginatedResponse
from app.schemas.base import PaginationParameters


class UserBase(BaseSchema):
    """Base user schema with common fields."""

    email: EmailStr = Field(description="User email address")
    full_name: str | None = Field(
        default=None, max_length=255, description="Display name"
    )
    is_active: bool = Field(default=True, description="Whether user is active")
    role: str = Field(default="user", description="User role")
    is_verified: bool = Field(default=False, description="Whether email is verified")


class UserCreate(UserBase):
    """Schema for creating a new user."""

    password: str = Field(min_length=8, max_length=128, description="Password")
    tenant_id: UUID | None = Field(
        default=None, description="Tenant ID (for superuser)"
    )


class UserUpdate(BaseSchema):
    """Schema for updating a user."""

    email: EmailStr | None = Field(default=None, description="Email address")
    full_name: str | None = Field(
        default=None, max_length=255, description="Display name"
    )
    is_active: bool | None = Field(default=None, description="Active status")
    role: str | None = Field(default=None, description="User role")


class UserUpdateMe(BaseSchema):
    """Schema for users updating their own profile."""

    full_name: str | None = Field(
        default=None, max_length=255, description="Display name"
    )
    email: EmailStr | None = Field(default=None, description="Email address")
    phone: PhoneNumberString = Field(
        default=None,
        max_length=16,
        description="Phone number in E.164 format",
    )
    current_password: str | None = Field(
        default=None,
        min_length=8,
        max_length=128,
        description="Current password for verification",
    )
    new_password: str | None = Field(
        default=None,
        min_length=8,
        max_length=128,
        description="New password (optional)",
    )


class UserResponse(UserBase):
    """Schema for user response (no sensitive data)."""

    id: UUID = Field(description="User ID")
    tenant_id: UUID | None = Field(description="Tenant ID")
    phone: PhoneNumberString = Field(
        default=None,
        max_length=16,
        description="Phone number in E.164 format",
    )
    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime = Field(description="Last update timestamp")


class UserListResponse(PaginatedResponse[UserResponse]):
    """Paginated list of users."""

    pass


class UserListParameters(PaginationParameters):
    """Query parameters for listing users."""

    is_active: bool | None = Field(default=None, description="Filter by active status")
    # Typed as the enum, not `str`. The endpoint used to do the conversion
    # itself, so an unknown role raised `ValueError` deep in the handler and
    # came back as a 500 rather than a 422.
    role: UserRole | None = Field(default=None, description="Filter by user role")
    search: str | None = Field(default=None, description="Search in email/name")
