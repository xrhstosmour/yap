"""User management API routes.

This module provides user management endpoints for
listing, creating, managing users, and GDPR self-service.
"""

from __future__ import annotations

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from fastapi.requests import Request
from fastapi.responses import Response

from app.core.logging import get_logger
from app.core.pagination import PAGINATION_HEADERS_SPEC
from app.core.pagination import PaginatedResponse
from app.dependencies import CurrentUser
from app.dependencies import SessionDependency
from app.dependencies import SuperuserUser
from app.schemas.user import UserCreate
from app.schemas.user import UserListParameters
from app.schemas.user import UserListResponse
from app.schemas.user import UserResponse
from app.schemas.user import UserUpdate
from app.schemas.user import UserUpdateMe
from app.services.user_service import UserService
from app.services.user_service import UserServiceError

router = APIRouter(prefix="/users", tags=["Users"])
logger = get_logger("api.users")


@router.get(
    "",
    response_model=UserListResponse,
    responses=PAGINATION_HEADERS_SPEC,  # type: ignore[arg-type]
    summary="List users",
    description="List all users with pagination and filtering. Admin only.",
)
async def list_users(
    parameters: Annotated[UserListParameters, Depends()],
    current_user: SuperuserUser,
    session: SessionDependency,
    request: Request,
) -> PaginatedResponse:
    """List all users (admin only).

    Returns paginated list of users with optional filtering
    by active status, admin status, or search query.
    """
    service = UserService(session)

    from app.models.user import UserRole

    role = UserRole(parameters.role) if parameters.role else None
    users, total = await service.list_users(
        skip=parameters.skip,
        limit=parameters.limit,
        is_active=parameters.is_active,
        role=role,
        search=parameters.search,
    )

    page = (parameters.skip // parameters.limit) + 1 if parameters.limit > 0 else 1
    pages = (
        (total + parameters.limit - 1) // parameters.limit
        if parameters.limit > 0
        else 1
    )

    return PaginatedResponse(
        content=UserListResponse(
            data=[UserResponse.model_validate(u) for u in users],
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


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user",
    description="Get information about the currently authenticated user.",
)
async def get_me(current_user: CurrentUser) -> UserResponse:
    """Get current user profile."""
    return UserResponse.model_validate(current_user)


@router.patch(
    "/me",
    response_model=UserResponse,
    summary="Update current user",
    description="Update the currently authenticated user's profile.",
)
async def update_me(
    data: UserUpdateMe,
    current_user: CurrentUser,
    session: SessionDependency,
) -> UserResponse:
    """Update current user's profile."""
    service = UserService(session)
    try:
        user = await service.update_profile(current_user, data)
    except UserServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    return UserResponse.model_validate(user)


@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete own account",
    description=(
        "Permanently delete the authenticated user's account (GDPR Article 17)."
    ),
)
async def delete_me(
    current_user: CurrentUser,
    session: SessionDependency,
) -> None:
    """Self-service account deletion (right to erasure).

    Soft-deletes the account and immediately invalidates all
    outstanding JWTs by bumping `token_version`. The action is
    irreversible from the user's perspective and is logged for
    compliance.
    """
    service = UserService(session)
    await service.delete_me(current_user)


@router.get(
    "/me/export",
    summary="Export personal data",
    description="Download all personal data held for the account (GDPR Article 20).",
)
async def export_my_data(
    current_user: CurrentUser,
    session: SessionDependency,
) -> Response:
    """Personal data export (right to data portability).

    Returns a JSON attachment containing the user's profile, API key
    metadata (no secrets), and audit activity. The export is logged
    for compliance.
    """
    service = UserService(session)
    data = await service.export_my_data(current_user)
    return Response(
        content=json.dumps(data, default=str),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="my-data.json"',
            "Cache-Control": "no-store, private",
            "Pragma": "no-cache",
        },
    )


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Get user",
    description="Get a specific user by ID. Admin only.",
)
async def get_user(
    user_id: UUID,
    current_user: SuperuserUser,
    session: SessionDependency,
) -> UserResponse:
    """Get user by ID (admin only)."""
    service = UserService(session)
    user = await service.get_by_id(user_id)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return UserResponse.model_validate(user)


@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create user",
    description="Create a new user. Admin only.",
)
async def create_user(
    data: UserCreate,
    current_user: SuperuserUser,
    session: SessionDependency,
) -> UserResponse:
    """Create a new user (admin only)."""
    service = UserService(session)

    try:
        user = await service.create(data, created_by=current_user.id)
        return UserResponse.model_validate(user)
    except (ValueError, UserServiceError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="Update user",
    description="Update a user. Admin only.",
)
async def update_user(
    user_id: UUID,
    data: UserUpdate,
    current_user: SuperuserUser,
    session: SessionDependency,
) -> UserResponse:
    """Update a user (admin only)."""
    service = UserService(session)
    try:
        user = await service.update(user_id, data, updated_by=current_user.id)
    except UserServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return UserResponse.model_validate(user)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete user",
    description="Delete a user. Admin only.",
)
async def delete_user(
    user_id: UUID,
    current_user: SuperuserUser,
    session: SessionDependency,
) -> None:
    """Delete a user (admin only)."""
    service = UserService(session)
    deleted = await service.delete(user_id, deleted_by=current_user.id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )


@router.post(
    "/{user_id}/revoke-sessions",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke all user sessions",
    description=(
        "Increment token version for a user, invalidating all active JWT "
        "sessions immediately. Admin only."
    ),
)
async def revoke_user_sessions(
    user_id: UUID,
    current_user: SuperuserUser,
    session: SessionDependency,
) -> None:
    """Revoke all sessions for a user (admin only)."""
    service = UserService(session)
    revoked = await service.revoke_all_sessions(user_id, current_user)

    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
