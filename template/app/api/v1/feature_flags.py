"""Feature flag API routes for admin management.

Provides superuser-only endpoints for creating, listing, updating,
and toggling feature flags at runtime.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from fastapi.requests import Request

from app.core.logging import get_logger
from app.core.pagination import PAGINATION_HEADERS_SPEC
from app.core.pagination import PaginatedResponse
from app.dependencies import SessionDependency
from app.dependencies import SuperuserUser
from app.schemas.base import PaginationParameters
from app.schemas.feature_flag import FeatureFlagCreate
from app.schemas.feature_flag import FeatureFlagListResponse
from app.schemas.feature_flag import FeatureFlagResponse
from app.schemas.feature_flag import FeatureFlagToggle
from app.schemas.feature_flag import FeatureFlagUpdate
from app.services.feature_flag_service import FeatureFlagService
from app.services.feature_flag_service import FeatureFlagServiceError

router = APIRouter(prefix="/feature-flags", tags=["Feature Flags"])
logger = get_logger("api.feature_flags")
_FLAG_NOT_FOUND = "Feature flag not found"


@router.get(
    "",
    response_model=FeatureFlagListResponse,
    responses=PAGINATION_HEADERS_SPEC,  # type: ignore[arg-type]
    summary="List feature flags",
    description="List all feature flags with pagination. Admin only.",
)
async def list_feature_flags(
    parameters: Annotated[PaginationParameters, Depends()],
    current_user: SuperuserUser,
    session: SessionDependency,
    request: Request,
) -> PaginatedResponse:
    """List all feature flags (admin only).

    Returns paginated list of feature flags.
    """
    service = FeatureFlagService(session)
    flags, total = await service.list_flags(
        skip=parameters.skip,
        limit=parameters.limit,
    )

    page = (parameters.skip // parameters.limit) + 1 if parameters.limit > 0 else 1
    pages = (
        (total + parameters.limit - 1) // parameters.limit
        if parameters.limit > 0
        else 1
    )

    return PaginatedResponse(
        content=FeatureFlagListResponse(
            data=[FeatureFlagResponse.model_validate(f) for f in flags],
            total=total,
            page=page,
            page_size=parameters.limit,
            pages=pages,
        ).model_dump(),
        total=total,
        skip=parameters.skip,
        limit=parameters.limit,
        request=request,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create feature flag",
    description="Create a new feature flag. Admin only.",
)
async def create_feature_flag(
    data: FeatureFlagCreate,
    current_user: SuperuserUser,
    session: SessionDependency,
) -> FeatureFlagResponse:
    """Create a new feature flag (admin only)."""
    service = FeatureFlagService(session)

    try:
        flag = await service.create_flag(data)
        return FeatureFlagResponse.model_validate(flag)
    except (ValueError, FeatureFlagServiceError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.get(
    "/{name}",
    summary="Get feature flag",
    description="Get a specific feature flag by name. Admin only.",
)
async def get_feature_flag(
    name: str,
    current_user: SuperuserUser,
    session: SessionDependency,
) -> FeatureFlagResponse:
    """Get a feature flag by name (admin only)."""
    service = FeatureFlagService(session)
    flag = await service.get_by_name(name)

    if not flag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_FLAG_NOT_FOUND,
        )

    return FeatureFlagResponse.model_validate(flag)


@router.patch(
    "/{name}",
    summary="Update feature flag",
    description="Update a feature flag. Admin only.",
)
async def update_feature_flag(
    name: str,
    data: FeatureFlagUpdate,
    current_user: SuperuserUser,
    session: SessionDependency,
) -> FeatureFlagResponse:
    """Update a feature flag (admin only)."""
    service = FeatureFlagService(session)
    flag = await service.update_flag(name, data)

    if not flag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_FLAG_NOT_FOUND,
        )

    return FeatureFlagResponse.model_validate(flag)


@router.post(
    "/{name}/toggle",
    summary="Toggle feature flag",
    description="Enable or disable a feature flag. Admin only.",
)
async def toggle_feature_flag(
    name: str,
    data: FeatureFlagToggle,
    current_user: SuperuserUser,
    session: SessionDependency,
) -> FeatureFlagResponse:
    """Toggle a feature flag's state (admin only)."""
    service = FeatureFlagService(session)
    flag = await service.toggle_flag(name, data.state)

    if not flag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_FLAG_NOT_FOUND,
        )

    return FeatureFlagResponse.model_validate(flag)


@router.delete(
    "/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete feature flag",
    description="Delete a feature flag. Admin only.",
)
async def delete_feature_flag(
    name: str,
    current_user: SuperuserUser,
    session: SessionDependency,
) -> None:
    """Delete a feature flag (admin only)."""
    service = FeatureFlagService(session)
    deleted = await service.delete_flag(name)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_FLAG_NOT_FOUND,
        )
