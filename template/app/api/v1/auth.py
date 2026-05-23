"""Authentication API routes.

This module provides authentication endpoints including
login, registration, and token refresh.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from fastapi.security import OAuth2PasswordRequestForm

from app.core.logging import get_logger
from app.dependencies import CurrentUser
from app.dependencies import SessionDep
from app.schemas.auth import PasswordChangeRequest
from app.schemas.auth import RefreshTokenRequest
from app.schemas.auth import RegisterRequest
from app.schemas.auth import TokenResponse
from app.schemas.user import UserResponse
from app.services.auth_service import AuthService
from app.services.auth_service import EmailAlreadyExistsError
from app.services.auth_service import InvalidCredentialsError
from app.services.auth_service import UserInactiveError

router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = get_logger("api.auth")


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    description="Create a new user account and return access tokens.",
)
async def register(
    data: RegisterRequest,
    session: SessionDep,
) -> TokenResponse:
    """Register a new user account.

    Creates a new user with the provided credentials
    and returns JWT tokens for immediate authentication.
    """
    service = AuthService(session)

    try:
        user = await service.register(data)
        return service.create_tokens(user)
    except EmailAlreadyExistsError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login",
    description="Authenticate with email and password to get access tokens.",
)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: SessionDep,
) -> TokenResponse:
    """Authenticate user and return tokens (username=email, password=password)."""
    service = AuthService(session)

    try:
        user = await service.authenticate(form_data.username, form_data.password)
        return service.create_tokens(user)
    except InvalidCredentialsError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )
    except UserInactiveError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh token",
    description="Exchange a refresh token for new access tokens.",
)
async def refresh_token(
    data: RefreshTokenRequest,
    session: SessionDep,
) -> TokenResponse:
    """Refresh access token using refresh token.

    Validates the refresh token and returns new access/refresh
    token pair. The old refresh token is invalidated.
    """
    service = AuthService(session)

    try:
        return await service.refresh_tokens(data.refresh_token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change password",
    description="Change the current user's password.",
)
async def change_password(
    data: PasswordChangeRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> None:
    """Change the current user's password.

    Requires the current password for verification.
    """
    service = AuthService(session)

    try:
        await service.change_password(
            current_user,
            data.current_password,
            data.new_password,
        )
    except InvalidCredentialsError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user",
    description="Get information about the currently authenticated user.",
)
async def get_me(current_user: CurrentUser) -> UserResponse:
    """Get current authenticated user.

    Returns the authenticated user's profile without sensitive fields
    such as hashed_password.
    """
    return UserResponse.model_validate(current_user)
