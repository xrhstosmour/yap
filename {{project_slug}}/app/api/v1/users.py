"""User management API routes.

This module provides user management endpoints for
listing, creating, and managing users.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import status

from app.core.logging import get_logger
from app.dependencies import CurrentUser
from app.dependencies import SessionDep
from app.dependencies import SuperuserUser
from app.schemas.user import UserCreate
from app.schemas.user import UserListParams
from app.schemas.user import UserListResponse
from app.schemas.user import UserResponse
from app.schemas.user import UserUpdate
from app.schemas.user import UserUpdateMe
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["Users"])
logger = get_logger("api.users")


@router.get(
    "",
    response_model=UserListResponse,
    summary="List users",
    description="List all users with pagination and filtering. Admin only.",
)
async def list_users(
    params: UserListParams,
    current_user: SuperuserUser,
    session: SessionDep,
) -> UserListResponse:
    """List all users (admin only).

    Returns paginated list of users with optional filtering
    by active status, admin status, or search query.
    """
    service = UserService(session)

    users, total = await service.list_users(
        skip=params.skip,
        limit=params.limit,
        is_active=params.is_active,
        is_superuser=params.is_superuser,
        search=params.search,
    )

    page = (params.skip // params.limit) + 1 if params.limit > 0 else 1
    pages = (total + params.limit - 1) // params.limit if params.limit > 0 else 1

    return UserListResponse(
        items=[UserResponse.model_validate(u) for u in users],
        total=total,
        page=page,
        page_size=params.limit,
        pages=pages,
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
    session: SessionDep,
) -> UserResponse:
    """Update current user's profile."""
    service = UserService(session)
    user = await service.update_profile(current_user, data)
    return UserResponse.model_validate(user)


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Get user",
    description="Get a specific user by ID. Admin only.",
)
async def get_user(
    user_id: UUID,
    current_user: SuperuserUser,
    session: SessionDep,
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
    session: SessionDep,
) -> UserResponse:
    """Create a new user (admin only)."""
    service = UserService(session)

    try:
        user = await service.create(data, created_by=current_user.id)
        return UserResponse.model_validate(user)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


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
    session: SessionDep,
) -> UserResponse:
    """Update a user (admin only)."""
    service = UserService(session)
    user = await service.update(user_id, data, updated_by=current_user.id)

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
    session: SessionDep,
) -> None:
    """Delete a user (admin only)."""
    service = UserService(session)
    deleted = await service.delete(user_id, deleted_by=current_user.id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
