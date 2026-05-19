"""Pydantic schemas for feature flag API responses and requests."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema
from app.schemas.base import PaginatedResponse


class FeatureFlagCreate(BaseSchema):
    """Request schema for creating a new feature flag.

    Attributes:
        name: Unique flag identifier
        state: Initial enabled/disabled state
        description: Optional human-readable description
    """

    name: str = Field(min_length=1, max_length=255)
    state: bool = False
    description: str | None = Field(default=None, max_length=500)


class FeatureFlagUpdate(BaseSchema):
    """Request schema for updating a feature flag.

    Attributes:
        state: New enabled/disabled state
        description: Updated description
    """

    state: bool | None = None
    description: str | None = Field(default=None, max_length=500)


class FeatureFlagToggle(BaseSchema):
    """Request schema for toggling a feature flag.

    Attributes:
        state: Target state to set
    """

    state: bool


class FeatureFlagResponse(BaseSchema):
    """Response schema for a single feature flag."""

    id: UUID
    name: str
    state: bool
    description: str | None = None
    created_at: datetime
    updated_at: datetime


class FeatureFlagListResponse(PaginatedResponse[FeatureFlagResponse]):
    """Paginated list response for feature flags."""

    pass
