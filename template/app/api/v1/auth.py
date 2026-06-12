"""Authentication API routes.

This module provides authentication endpoints including
login, registration, token refresh, and email verification.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import status
from fastapi.security import OAuth2PasswordRequestForm

from app.core.logging import get_logger
from app.core.security import verify_email_verification_token
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
from app.services.auth_service import UserNotFoundError

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


@router.post(
    "/send-verification-email",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Send verification email",
    description="Send an email verification link to the current user's address.",
)
async def send_verification_email(
    current_user: CurrentUser,
    session: SessionDep,
) -> None:
    """Send an email verification link to the authenticated user.

    Generates a single-use token and dispatches a Celery task to
    deliver the verification email. Returns 400 if already verified.
    """
    if current_user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email address is already verified.",
        )

    service = AuthService(session)
    await service.send_verification_email(current_user)


@router.get(
    "/verify-email",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Verify email address",
    description="Verify email address using the token from the verification email.",
)
async def verify_email(
    session: SessionDep,
    token: Annotated[str | None, Query(description="Verification token from email")] = None,
) -> None:
    """Verify a user's email address.

    Validates the single-use token and marks the user as verified.
    Returns 400 if the token is missing, expired, or already used.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token.",
        )

    user_id = await verify_email_verification_token(token)

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token.",
        )

    service = AuthService(session)
    try:
        await service.verify_user(user_id)
    except UserNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token.",
        )
