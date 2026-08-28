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


class LogoutRequest(BaseSchema):
    """Logout request.

    Attributes:
        refresh_token: Optional refresh token to invalidate together
            with the current access token.
    """

    refresh_token: str | None = Field(
        default=None,
        description="Optional refresh token to revoke during logout",
    )


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


class GoogleAuthUrlResponse(BaseSchema):
    """Google OAuth 2.0 authorization URL response.

    Returned by `GET /auth/google` so the client can redirect the
    user to Google's consent screen.

    Attributes:
        url: Full Google OAuth 2.0 authorization URL including CSRF state.
    """

    url: str = Field(description="Google OAuth 2.0 authorization URL")


class GoogleCallbackRequest(BaseSchema):
    """Google OAuth callback request.

    Request body for `POST /auth/google/callback` after Google
    redirects the user back to the application.

    Attributes:
        code: Authorization code received from Google redirect.
        state: CSRF state token returned by Google.
        redirect_uri: Must exactly match the URI used in the
            authorization request.
    """

    code: str = Field(description="Authorization code from Google redirect")
    state: str = Field(description="CSRF state token returned by Google")
    redirect_uri: str = Field(
        description="Redirect URI used in the authorization request"
    )


class WSTicketResponse(BaseSchema):
    """WebSocket authentication ticket response.

    Returned by `POST /auth/ws-ticket`. The ticket is opaque, single-use,
    and must be presented as the `ticket` query parameter when opening a
    WebSocket connection, in place of a JWT.

    Attributes:
        ticket: Opaque single-use ticket string.
        expires_in: Seconds until the ticket expires if unused.
    """

    ticket: str = Field(description="Opaque single-use WebSocket auth ticket")
    expires_in: int = Field(description="Seconds until the ticket expires")


class LoginResponse(BaseSchema):
    """Login response, either JWT tokens or a 2FA challenge.

    When 2FA is disabled, access_token and refresh_token are populated.
    When 2FA is required, requires_2fa is True and challenge_token is set.

    Attributes:
        access_token: JWT access token (present when 2FA not required).
        refresh_token: JWT refresh token (present when 2FA not required).
        token_type: Token type (always "bearer").
        expires_in: Access token lifetime in seconds (present when 2FA not required).
        requires_2fa: True if the user must complete a 2FA challenge.
        challenge_token: Opaque challenge token to pass to POST /auth/2fa/verify.
    """

    access_token: str | None = Field(default=None, description="JWT access token")
    refresh_token: str | None = Field(default=None, description="JWT refresh token")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: int | None = Field(
        default=None, description="Access token lifetime in seconds"
    )
    requires_2fa: bool = Field(
        default=False, description="Whether a 2FA challenge is required"
    )
    challenge_token: str | None = Field(
        default=None, description="Opaque challenge token for 2FA verification"
    )


class TwoFactorEnrollResponse(BaseSchema):
    """Response from POST /auth/2fa/enroll.

    Attributes:
        qr_data_url: Base64-encoded PNG QR code for authenticator app setup.
        recovery_codes: One-time backup codes (shown once, store securely).
    """

    qr_data_url: str = Field(description="Data URL of the QR code PNG image")
    recovery_codes: list[str] = Field(
        description="One-time recovery codes; store these in a safe place"
    )


class TwoFactorConfirmRequest(BaseSchema):
    """Request for POST /auth/2fa/confirm.

    Attributes:
        totp_code: 6-digit code from the authenticator app.
    """

    totp_code: str = Field(min_length=6, max_length=6, description="6-digit TOTP code")


class TwoFactorVerifyRequest(BaseSchema):
    """Request for POST /auth/2fa/verify.

    Attributes:
        challenge_token: Token received from the login response.
        totp_code: 6-digit TOTP code (mutually exclusive with recovery_code).
        recovery_code: Backup recovery code (mutually exclusive with totp_code).
    """

    challenge_token: str = Field(description="Challenge token from login response")
    totp_code: str | None = Field(
        default=None, min_length=6, max_length=6, description="6-digit TOTP code"
    )
    recovery_code: str | None = Field(
        default=None,
        min_length=9,
        max_length=9,
        description="Recovery code (XXXX-XXXX)",
    )


class TwoFactorDisableRequest(BaseSchema):
    """Request for DELETE /auth/2fa/disable.

    Attributes:
        totp_code: 6-digit TOTP code to confirm disabling 2FA.
    """

    totp_code: str = Field(
        min_length=6, max_length=6, description="6-digit TOTP code to confirm"
    )


class RecoveryCodesResponse(BaseSchema):
    """Response containing regenerated recovery codes.

    Attributes:
        recovery_codes: New one-time recovery codes (shown once).
    """

    recovery_codes: list[str] = Field(
        description="New one-time recovery codes; store these in a safe place"
    )


class MagicLinkRequest(BaseSchema):
    """Request for POST /auth/magic-link.

    Attributes:
        email: Email address to send the magic link to.
    """

    email: str = Field(description="Email address to send the magic link to")


class MagicLinkVerifyRequest(BaseSchema):
    """Request for POST /auth/magic-link/verify.

    Attributes:
        token: Magic link token from the email.
    """

    token: str = Field(description="Magic link token from the email")


class WebAuthnRegisterBeginResponse(BaseSchema):
    """Response from POST /auth/webauthn/register/begin.

    Attributes:
        options: The ``PublicKeyCredentialCreationOptions`` dict.
    """

    options: dict = Field(description="WebAuthn registration options for the frontend")


class WebAuthnRegisterCompleteRequest(BaseSchema):
    """Request for POST /auth/webauthn/register/complete.

    Attributes:
        credential: The credential object from ``navigator.credentials.create()``.
        device_name: Optional human-readable name for the passkey.
    """

    credential: dict = Field(
        description="Credential from navigator.credentials.create()"
    )
    device_name: str | None = Field(
        default=None, description="Human-readable device name"
    )


class WebAuthnRegisterCompleteResponse(BaseSchema):
    """Response from POST /auth/webauthn/register/complete.

    Attributes:
        credential_id: The stored credential ID.
        device_name: Human-readable name for the passkey.
    """

    credential_id: str = Field(description="Stored credential ID")
    device_name: str = Field(description="Human-readable device name")


class WebAuthnLoginBeginRequest(BaseSchema):
    """Request for POST /auth/webauthn/login/begin.

    Attributes:
        email: Optional email to narrow which credentials are accepted.
    """

    email: str | None = Field(default=None, description="Email to narrow credentials")


class WebAuthnLoginBeginResponse(BaseSchema):
    """Response from POST /auth/webauthn/login/begin.

    Attributes:
        options: The ``PublicKeyCredentialRequestOptions`` dict.
        challenge_session_key: Opaque key that must be echoed back in the
            completion request. Always present. It used to be omitted when
            the address named a real account, which made its absence an
            account-existence oracle on an unauthenticated endpoint.
    """

    options: dict = Field(
        description="WebAuthn authentication options for the frontend"
    )
    challenge_session_key: str = Field(
        description="Echo this back in the completion request"
    )


class WebAuthnLoginCompleteRequest(BaseSchema):
    """Request for POST /auth/webauthn/login/complete.

    Attributes:
        credential: The credential object from ``navigator.credentials.get()``.
        challenge_session_key: Required when the begin response included one
            (i.e. the flow was started without an email address).
    """

    credential: dict = Field(description="Credential from navigator.credentials.get()")
    challenge_session_key: str | None = Field(
        default=None,
        description="Session key from the begin response (anonymous flows only)",
    )
