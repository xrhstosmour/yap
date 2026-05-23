"""Authentication schemas for request/response validation.

This module defines Pydantic schemas for authentication
endpoints including login, registration, and token refresh.
"""

from __future__ import annotations

from pydantic import EmailStr
from pydantic import Field

from app.schemas.base import BaseSchema


class LoginRequest(BaseSchema):
    """User login request.

    Request body for user authentication with email/password.

    Attributes:
        email: User's email address
        password: User's password
    """

    email: EmailStr = Field(description="User email address")
    password: str = Field(min_length=8, max_length=128, description="User password")


class RegisterRequest(BaseSchema):
    """User registration request.

    Request body for creating a new user account.

    Attributes:
        email: Desired email address (must be unique)
        password: Desired password (min 8 characters)
        full_name: Optional display name
    """

    email: EmailStr = Field(description="Email address (must be unique)")
    password: str = Field(
        min_length=8, max_length=128, description="Password (min 8 characters)"
    )
    full_name: str | None = Field(
        default=None, max_length=255, description="Display name"
    )


class TokenResponse(BaseSchema):
    """JWT token response.

    Response containing access and refresh tokens.

    Attributes:
        access_token: JWT access token (short-lived)
        refresh_token: JWT refresh token (long-lived)
        token_type: Token type (always "bearer")
        expires_in: Access token lifetime in seconds
    """

    access_token: str = Field(description="JWT access token")
    refresh_token: str = Field(description="JWT refresh token")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: int = Field(description="Access token lifetime in seconds")


class RefreshTokenRequest(BaseSchema):
    """Token refresh request.

    Request body for exchanging refresh token for new access token.

    Attributes:
        refresh_token: Valid refresh token
    """

    refresh_token: str = Field(description="Refresh token")


class PasswordChangeRequest(BaseSchema):
    """Password change request.

    Request body for changing user's password.

    Attributes:
        current_password: User's current password
        new_password: New password (min 8 characters)
    """

    current_password: str = Field(
        min_length=8, max_length=128, description="Current password"
    )
    new_password: str = Field(min_length=8, max_length=128, description="New password")


class PasswordResetRequest(BaseSchema):
    """Password reset request.

    Request body for initiating password reset flow.

    Attributes:
        email: Email address of account to reset
    """

    email: EmailStr = Field(description="Email address of account to reset")


class PasswordResetConfirmRequest(BaseSchema):
    """Password reset confirmation.

    Request body for completing password reset with token.

    Attributes:
        token: Reset token from email
        new_password: New password (min 8 characters)
    """

    token: str = Field(description="Reset token from email")
    new_password: str = Field(min_length=8, max_length=128, description="New password")
