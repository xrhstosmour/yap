"""Authentication API routes.

This module provides authentication endpoints including
login, registration, token refresh, email verification,
password reset, and Google OAuth.
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
from app.schemas.auth import GoogleAuthUrlResponse
from app.schemas.auth import GoogleCallbackRequest
from app.schemas.auth import LoginResponse
from app.schemas.auth import PasswordChangeRequest
from app.schemas.auth import PasswordResetConfirmRequest
from app.schemas.auth import PasswordResetRequest
from app.schemas.auth import RecoveryCodesResponse
from app.schemas.auth import RefreshTokenRequest
from app.schemas.auth import RegisterRequest
from app.schemas.auth import TokenResponse
from app.schemas.auth import TwoFactorConfirmRequest
from app.schemas.auth import TwoFactorDisableRequest
from app.schemas.auth import TwoFactorEnrollResponse
from app.schemas.auth import TwoFactorVerifyRequest
from app.schemas.user import UserResponse
from app.services.auth_service import AuthenticationError
from app.services.auth_service import AuthService
from app.services.auth_service import EmailAlreadyExistsError
from app.services.auth_service import InvalidCredentialsError
from app.services.auth_service import UserInactiveError
from app.services.auth_service import UserNotFoundError

router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = get_logger("api.auth")

INVALID_VERIFICATION_TOKEN = "Invalid or expired verification token."


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
    response_model=LoginResponse,
    summary="Login",
    description=(
        "Authenticate with email and password. "
        "Returns tokens directly, or a 2FA challenge if the account has 2FA enabled."
    ),
)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: SessionDep,
) -> LoginResponse:
    """Authenticate user and return tokens or a 2FA challenge."""
    from app.services.two_factor_service import TwoFactorAuthService

    service = AuthService(session)

    try:
        user = await service.authenticate(form_data.username, form_data.password)
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

    # If 2FA is active, issue a challenge token instead of full tokens.
    if user.is_2fa_enabled:
        totp_service = TwoFactorAuthService(session)
        challenge_token = await totp_service.issue_challenge(user)
        return LoginResponse(requires_2fa=True, challenge_token=challenge_token)

    tokens = service.create_tokens(user)
    return LoginResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        token_type=tokens.token_type,
        expires_in=tokens.expires_in,
    )


@router.post(
    "/2fa/enroll",
    response_model=TwoFactorEnrollResponse,
    status_code=status.HTTP_200_OK,
    summary="Begin 2FA enrollment",
    description=(
        "Generate a TOTP secret and QR code. "
        "Call POST /auth/2fa/confirm with a valid code to activate 2FA."
    ),
)
async def enroll_2fa(
    current_user: CurrentUser,
    session: SessionDep,
) -> TwoFactorEnrollResponse:
    """Begin TOTP 2FA enrollment for the current user.

    Returns a QR code data URL and plaintext recovery codes.
    Recovery codes are shown only once — store them securely.
    """
    from app.services.two_factor_service import TwoFactorAlreadyEnabledError
    from app.services.two_factor_service import TwoFactorAuthService

    service = TwoFactorAuthService(session)
    try:
        _secret, qr_data_url, recovery_codes = await service.begin_enrollment(current_user)
    except TwoFactorAlreadyEnabledError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    return TwoFactorEnrollResponse(
        qr_data_url=qr_data_url,
        recovery_codes=recovery_codes,
    )


@router.post(
    "/2fa/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Confirm 2FA enrollment",
    description="Verify the first TOTP code to activate 2FA on the account.",
)
async def confirm_2fa(
    data: TwoFactorConfirmRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> None:
    """Confirm TOTP enrollment by verifying the first valid code."""
    from app.services.two_factor_service import InvalidTOTPError
    from app.services.two_factor_service import TwoFactorAlreadyEnabledError
    from app.services.two_factor_service import TwoFactorAuthService
    from app.services.two_factor_service import TwoFactorError
    from app.services.two_factor_service import TwoFactorRateLimitError

    service = TwoFactorAuthService(session)
    try:
        await service.confirm_enrollment(current_user, data.totp_code)
    except TwoFactorRateLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e),
            headers={"Retry-After": "300"},
        )
    except (InvalidTOTPError, TwoFactorError, TwoFactorAlreadyEnabledError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post(
    "/2fa/verify",
    response_model=TokenResponse,
    summary="Complete 2FA login",
    description=(
        "Exchange a 2FA challenge token + TOTP code (or recovery code) "
        "for full JWT tokens."
    ),
)
async def verify_2fa(
    data: TwoFactorVerifyRequest,
    session: SessionDep,
) -> TokenResponse:
    """Complete 2FA login by verifying the challenge + TOTP or recovery code."""
    from app.services.two_factor_service import InvalidTOTPError
    from app.services.two_factor_service import TwoFactorAuthService
    from app.services.two_factor_service import TwoFactorError
    from app.services.two_factor_service import TwoFactorRateLimitError

    if not data.totp_code and not data.recovery_code:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either totp_code or recovery_code is required.",
        )

    service = TwoFactorAuthService(session)
    auth_service = AuthService(session)

    try:
        if data.totp_code:
            user = await service.verify_challenge(data.challenge_token, data.totp_code)
        else:
            user = await service.verify_challenge_with_recovery(
                data.challenge_token,
                data.recovery_code,  # type: ignore[arg-type]
            )
    except TwoFactorRateLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e),
            headers={"Retry-After": "300"},
        )
    except (InvalidTOTPError, TwoFactorError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )

    return auth_service.create_tokens(user)


@router.delete(
    "/2fa/disable",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disable 2FA",
    description="Disable TOTP 2FA. Requires a valid TOTP code to confirm.",
)
async def disable_2fa(
    data: TwoFactorDisableRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> None:
    """Disable TOTP 2FA for the current user."""
    from app.services.two_factor_service import InvalidTOTPError
    from app.services.two_factor_service import TwoFactorAuthService
    from app.services.two_factor_service import TwoFactorError
    from app.services.two_factor_service import TwoFactorNotEnabledError
    from app.services.two_factor_service import TwoFactorRateLimitError

    service = TwoFactorAuthService(session)
    try:
        await service.disable(current_user, data.totp_code)
    except TwoFactorNotEnabledError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except TwoFactorRateLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e),
            headers={"Retry-After": "300"},
        )
    except (InvalidTOTPError, TwoFactorError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post(
    "/2fa/recovery-codes",
    response_model=RecoveryCodesResponse,
    summary="Regenerate recovery codes",
    description="Regenerate all recovery codes. Requires a valid TOTP code.",
)
async def regenerate_recovery_codes(
    data: TwoFactorConfirmRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> RecoveryCodesResponse:
    """Regenerate all 2FA recovery codes for the current user."""
    from app.services.two_factor_service import InvalidTOTPError
    from app.services.two_factor_service import TwoFactorAuthService
    from app.services.two_factor_service import TwoFactorError
    from app.services.two_factor_service import TwoFactorNotEnabledError
    from app.services.two_factor_service import TwoFactorRateLimitError

    service = TwoFactorAuthService(session)
    try:
        new_codes = await service.regenerate_recovery_codes(current_user, data.totp_code)
    except TwoFactorNotEnabledError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except TwoFactorRateLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e),
            headers={"Retry-After": "300"},
        )
    except (InvalidTOTPError, TwoFactorError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    return RecoveryCodesResponse(recovery_codes=new_codes)


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
            detail=INVALID_VERIFICATION_TOKEN,
        )

    user_id = await verify_email_verification_token(token)

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_VERIFICATION_TOKEN,
        )

    service = AuthService(session)
    try:
        await service.verify_user(user_id)
    except UserNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_VERIFICATION_TOKEN,
        )


@router.post(
    "/forgot-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Request password reset",
    description="Send a password reset link to the given email address.",
)
async def forgot_password(
    data: PasswordResetRequest,
    session: SessionDep,
) -> None:
    """Send a password reset email.

    Always returns 204 regardless of whether the address is registered,
    to prevent user enumeration. The reset link expires in 1 hour.
    """
    service = AuthService(session)
    await service.send_password_reset_email(data.email)


@router.post(
    "/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Confirm password reset",
    description="Reset password using the token from the reset email.",
)
async def reset_password(
    data: PasswordResetConfirmRequest,
    session: SessionDep,
) -> None:
    """Reset a user's password using a valid reset token.

    Consumes the single-use token and updates the password. All
    outstanding JWTs are invalidated by bumping `token_version`.
    Returns 400 if the token is missing, expired, or already used.
    """
    service = AuthService(session)
    try:
        await service.reset_password(data.token, data.new_password)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get(
    "/google",
    response_model=GoogleAuthUrlResponse,
    summary="Initiate Google OAuth",
    description="Get the Google OAuth 2.0 authorization URL to start the login flow.",
)
async def google_auth(
    session: SessionDep,
    redirect_uri: Annotated[
        str, Query(description="URI Google should redirect to after consent")
    ],
) -> GoogleAuthUrlResponse:
    """Get the Google OAuth 2.0 authorization URL.

    Generates a single-use CSRF state token backed by Redis and
    constructs the Google consent-screen URL. The caller must
    redirect the end-user to the returned URL.
    Returns 503 if Google OAuth credentials are not configured.
    """
    service = AuthService(session)
    try:
        url = await service.get_google_auth_url(redirect_uri)
        return GoogleAuthUrlResponse(url=url)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


@router.post(
    "/google/callback",
    response_model=TokenResponse,
    summary="Google OAuth callback",
    description="Exchange a Google authorization code for YAP access tokens.",
)
async def google_callback(
    data: GoogleCallbackRequest,
    session: SessionDep,
) -> TokenResponse:
    """Complete the Google OAuth login flow.

    Validates the CSRF state, exchanges the authorization code for a
    Google access token, fetches the user profile, then finds or
    creates a local account. Returns YAP JWT tokens on success.
    Returns 400 if the state is invalid or the code exchange fails.
    Returns 403 if the account is inactive.
    """
    service = AuthService(session)
    try:
        return await service.google_login(data.code, data.state, data.redirect_uri)
    except UserInactiveError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
