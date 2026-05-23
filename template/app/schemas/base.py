"""Base schemas with pagination support.

This module provides base Pydantic schemas for request/response
validation with pagination support.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class BaseSchema(BaseModel):
    """Base schema with common configuration.

    All Pydantic schemas should inherit from this class.
    Provides consistent model configuration across schemas.
    """

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        validate_assignment=True,
    )


class PaginationParams(BaseModel):
    """Pagination parameters for list endpoints.

    Standard pagination parameters that can be used
    with any list endpoint.

    Attributes:
        skip: Number of records to skip (offset)
        limit: Maximum number of records to return
        sort_by: Field to sort by
        sort_order: Sort direction (asc/desc)
    """

    skip: int = Field(default=0, ge=0, description="Number of records to skip")
    limit: int = Field(default=20, ge=1, le=100, description="Max records to return")
    sort_by: str | None = Field(default=None, description="Field to sort by")
    sort_order: str = Field(default="asc", pattern="^(asc|desc)$")


T = TypeVar("T")


class PaginatedResponse[T](BaseSchema):
    """Generic paginated response schema.

    Wraps list responses with pagination metadata.

    Attributes:
        data: List of data in the current page
        total: Total number of data across all pages
        page: Current page number (1-indexed)
        page_size: Number of data per page
        pages: Total number of pages
    """

    data: list[T] = Field(description="Data in the current page")
    total: int = Field(description="Total number of data")
    page: int = Field(description="Current page number")
    page_size: int = Field(description="Number of data per page")
    pages: int = Field(description="Total number of pages")


class MessageResponse(BaseSchema):
    """Simple message response.

    Used for simple confirmations that don't need data.
    """

    message: str = Field(description="Response message")


class ErrorDetail(BaseSchema):
    """Error detail for validation errors.

    Provides structured error information for API responses.
    """

    loc: list[str | int] = Field(description="Location of the error")
    msg: str = Field(description="Error message")
    type: str = Field(description="Error type")


class ErrorResponse(BaseSchema):
    """Standard error response schema.

    All API errors should return this structure.

    Attributes:
        detail: Error message or list of error details
    """

    detail: str | list[ErrorDetail] = Field(
        description="Error message or validation errors"
    )


class HealthResponse(BaseSchema):
    """Health check response.

    Returns the status of various system components.

    Attributes:
        status: Overall system status
        version: Application version
        database: Database connection status
        cache: Cache connection status
    """

    status: str = Field(description="Overall status")
    version: str = Field(description="Application version")
    database: str = Field(description="Database status")
    cache: str = Field(description="Cache status")
