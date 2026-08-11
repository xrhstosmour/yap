"""Authentication service for user authentication.

This module provides the AuthService class for handling
user authentication, registration, and token management.
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC
from datetime import datetime
from typing import Any
from uuid import UUID

from jwt import ExpiredSignatureError
from jwt import InvalidTokenError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import SYSTEM_TENANT_ID
from app.core.logging import get_logger
from app.core.security import DUMMY_PASSWORD_HASH
from app.core.security import blacklist_token
from app.core.security import create_access_token
from app.core.security import create_refresh_token
from app.core.security import decode_token
from app.core.security import generate_password_hash
from app.core.security import is_token_blacklisted
from app.core.security import verify_password
from app.core.settings import settings
from app.models.audit_log import AuditAction
from app.models.user import User
from app.models.user import UserRole
from app.repositories.audit_repository import AuditLogRepository
from app.repositories.oauth_account_repository import OAuthAccountRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import LoginResponse
from app.schemas.auth import RegisterRequest
from app.schemas.auth import TokenResponse

logger = get_logger("service.auth")


class AuthenticationError(Exception):
    """Base exception for authentication errors."""

    pass


class InvalidCredentialsError(AuthenticationError):
    """Invalid email or password."""

    pass


class EmailAlreadyExistsError(AuthenticationError):
    """Email is already registered."""

    pass


class UserNotFoundError(AuthenticationError):
    """User not found."""

    pass


class UserInactiveError(AuthenticationError):
    """User account is inactive."""

    pass


class AuthService:
    """Service for authentication operations.

    Handles user registration, login, token generation,
    and password management.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize auth service.

        Args:
            session: Async database session
        """
        self.session = session
        self.user_repository = UserRepository(session)
        self.audit_repository = AuditLogRepository(session)
        self.oauth_account_repository = OAuthAccountRepository(session)

    @staticmethod
    def _validate_password_strength(password: str) -> None:
        """Validate password meets minimum strength requirements.

        Template implementation — projects should customize this
        with their own password policy (e.g. zxcvbn, Pwned Passwords).

        Args:
            password: Plain-text password to validate.
        """
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")

    @staticmethod
    async def _blacklist_payload_token(payload: dict[str, Any]) -> None:
        """Blacklist token from decoded payload when claims are present.

        Args:
            payload: Decoded JWT payload.
        """
        token_identifier = payload.get("jti")
        token_expiration = payload.get("exp")

        if not isinstance(token_identifier, str):
            return
        if not isinstance(token_expiration, (int, float)):
            return

        expires_at = datetime.fromtimestamp(token_expiration, tz=UTC)
        await blacklist_token(token_identifier, expires_at)

    async def _update_password_and_invalidate(
        self, user: User, new_password: str
    ) -> None:
        """Hash a new password and invalidate all outstanding tokens.

        Args:
            user: User whose password is being changed.
            new_password: New plain-text password.
        """
        hashed = await asyncio.to_thread(generate_password_hash, new_password)
        await self.user_repository.update(
            user.id,
            {
                "hashed_password": hashed,
                "token_version": User.token_version + 1,
            },
        )

    async def _send_token_email(
        self,
        user: User,
        token_factory: Any,  # noqa: ANN401
        url_path: str,
        subject: str,
        template_name: str,
        url_field_name: str = "url",
    ) -> None:
        """Queue a single-use token email (verification, reset, magic link).

        Generates a Redis-backed token, builds the URL, and dispatches
        a Celery template email task.

        Args:
            user: The recipient user.
            token_factory: Awaitable that resolves to a token string
                (e.g. ``create_email_verification_token(user.id)``).
            url_path: URL path relative to FRONTEND_HOST (e.g. "/auth/verify-email").
            subject: Email subject line.
            template_name: Email template file name (e.g. "verification.html").
            url_field_name: Template context key for the generated URL
                (e.g. "verification_url", "reset_url", "login_url").
        """
        from app.tasks.email import send_template_email_task

        token = await token_factory
        url = f"{settings.FRONTEND_HOST}{url_path}?token={token}"

        # NOTE: send_template_email_task.delay() is a Celery sync call.
        # Celery tasks are designed to be dispatched synchronously with .delay()
        # or .apply_async() — this is correct even in an async context because
        # the task is serialized and handed off to the broker, not executed inline.
        try:
            send_template_email_task.delay(
                to_email=user.email,
                subject=subject,
                template_name=template_name,
                context={
                    "name": user.full_name or user.email,
                    url_field_name: url,
                    "project": settings.PROJECT_NAME,
                },
            )
        except Exception:
            logger.warning(
                "email_task_dispatch_failed",
                to_email=user.email,
                subject=subject,
            )

    async def register(
        self,
        data: RegisterRequest,
        tenant_id: UUID | None = None,
    ) -> User:
        """Register a new user.

        Args:
            data: Registration data
            tenant_id: Optional tenant to assign user to

        Returns:
            Created User instance

        Raises:
            EmailAlreadyExistsError: If email is taken
        """
        self._validate_password_strength(data.password)

        # Check if email exists.
        if await self.user_repository.email_exists(data.email):
            raise EmailAlreadyExistsError("Email already registered")

        # Create user.
        password_hash = await asyncio.to_thread(generate_password_hash, data.password)
        user = await self.user_repository.create_user(
            email=data.email,
            password_hash=password_hash,
            full_name=data.full_name,
            tenant_id=tenant_id,
            role=UserRole.USER,
        )

        logger.info("user_registered", user_id=str(user.id))

        return user

    async def authenticate(
        self,
        email: str,
        password: str,
        tenant_id: UUID | None = None,
    ) -> User:
        """Authenticate user with email and password.

        Args:
            email: User's email
            password: Plain text password
            tenant_id: Tenant context

        Returns:
            Authenticated User

        Raises:
            InvalidCredentialsError: If credentials are invalid
            UserInactiveError: If user is deactivated
        """
        # Get user by email.
        user = await self.user_repository.get_by_email(email)

        if not user:
            # Verify against dummy hash to prevent timing attacks.
            await asyncio.to_thread(verify_password, password, DUMMY_PASSWORD_HASH)
            raise InvalidCredentialsError("Invalid email or password")

        # Verify password.
        if not await asyncio.to_thread(verify_password, password, user.hashed_password):
            raise InvalidCredentialsError("Invalid email or password")

        # Check if active.
        if not user.is_active:
            raise UserInactiveError("User account is inactive")

        # Log successful login.
        await self.audit_repository.log_user_action_safe(
            action=AuditAction.LOGIN,
            user_id=user.id,
            tenant_id=user.tenant_id or SYSTEM_TENANT_ID,
            email=user.email,
        )

        logger.info("user_authenticated", user_id=str(user.id))

        return user

    def create_tokens(self, user: User) -> TokenResponse:
        """Create access and refresh tokens for user.

        Args:
            user: Authenticated user

        Returns:
            TokenResponse with access and refresh tokens
        """
        # Access token.
        access_token = create_access_token(
            subject=str(user.id),
            additional_claims={
                "email": user.email,
                "tenant_id": str(user.tenant_id) if user.tenant_id else None,
                "role": user.role.value,
                "token_version": user.token_version,
            },
        )

        # Refresh token.
        refresh_token = create_refresh_token(
            subject=str(user.id),
            additional_claims={"token_version": user.token_version},
        )

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    async def issue_login_response(self, user: User) -> LoginResponse:
        """Issue tokens, or a 2FA challenge if the account requires one.

        Every login path (password, magic link, OAuth, WebAuthn) must
        route through this instead of calling `create_tokens` directly,
        or a second factor the user configured is silently skipped.

        Args:
            user: Authenticated user.

        Returns:
            LoginResponse with tokens set, or `requires_2fa` and
            `challenge_token` set when a second factor is required.
        """
        if user.is_2fa_enabled:
            from app.services.two_factor_service import TwoFactorAuthService

            totp_service = TwoFactorAuthService(self.session)
            challenge_token = await totp_service.issue_challenge(user)
            return LoginResponse(requires_2fa=True, challenge_token=challenge_token)

        tokens = self.create_tokens(user)
        return LoginResponse(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            token_type=tokens.token_type,
            expires_in=tokens.expires_in,
        )

    async def refresh_tokens(self, refresh_token: str) -> TokenResponse:
        """Refresh access token using refresh token.

        Args:
            refresh_token: Valid refresh token

        Returns:
            New TokenResponse with fresh tokens

        Raises:
            AuthenticationError: If refresh token is invalid
        """
        try:
            payload = decode_token(refresh_token)

            # Verify it's a refresh token.
            if payload.get("type") != "refresh":
                raise AuthenticationError("Invalid token type")

            # Reject a refresh token already blacklisted by a prior
            # logout or rotation. Without this check, blacklisting on
            # logout is decorative: a captured refresh token keeps
            # minting fresh token pairs indefinitely.
            token_identifier = payload.get("jti")
            if isinstance(token_identifier, str) and await is_token_blacklisted(
                token_identifier
            ):
                raise AuthenticationError("Refresh token has been revoked")

            user_id = payload.get("sub")
            if not user_id:
                raise AuthenticationError("Invalid token")

            # Get user.
            user = await self.user_repository.get(UUID(user_id))
            if not user:
                raise UserNotFoundError("User not found")

            if not user.is_active:
                raise UserInactiveError("User account is inactive")

            token_version = payload.get("token_version")
            if token_version is not None and token_version != user.token_version:
                raise AuthenticationError("Token invalidated due to password change")

            token_response = self.create_tokens(user)
            # Only the refresh token is blacklisted.  Access tokens are
            # short-lived and expire naturally; blacklisting them adds
            # Redis overhead with minimal security benefit.
            await self._blacklist_payload_token(payload)

            return token_response

        except (
            InvalidTokenError,
            ExpiredSignatureError,
            UserNotFoundError,
            UserInactiveError,
            ValueError,
        ) as e:
            logger.warning("token_refresh_failed", error=str(e))
            raise AuthenticationError("Invalid refresh token") from e

    async def logout(
        self,
        user: User,
        access_payload: dict[str, Any],
        refresh_token: str | None = None,
    ) -> None:
        """Invalidate current access token and optional refresh token.

        Args:
            user: Authenticated user requesting logout.
            access_payload: Decoded access token payload.
            refresh_token: Optional refresh token to invalidate.

        Raises:
            AuthenticationError: If provided refresh token is invalid.
        """
        await self._blacklist_payload_token(access_payload)

        if refresh_token:
            try:
                refresh_payload = decode_token(refresh_token)
                if refresh_payload.get("type") != "refresh":
                    raise AuthenticationError("Invalid token type")

                refresh_subject = refresh_payload.get("sub")
                if str(refresh_subject) != str(user.id):
                    raise AuthenticationError("Invalid refresh token")

                await self._blacklist_payload_token(refresh_payload)
            except (InvalidTokenError, ExpiredSignatureError, ValueError) as error:
                raise AuthenticationError("Invalid refresh token") from error

        tenant_id = user.tenant_id or SYSTEM_TENANT_ID
        await self.audit_repository.log_user_action_safe(
            action=AuditAction.LOGOUT,
            user_id=user.id,
            tenant_id=tenant_id,
            email=user.email,
        )

    async def change_password(
        self,
        user: User,
        current_password: str,
        new_password: str,
    ) -> None:
        """Change user's password.

        Args:
            user: User changing password
            current_password: Current password
            new_password: New password

        Raises:
            InvalidCredentialsError: If current password is wrong
        """
        self._validate_password_strength(new_password)

        # Verify current password.
        if not await asyncio.to_thread(
            verify_password, current_password, user.hashed_password
        ):
            raise InvalidCredentialsError("Current password is incorrect")

        # Update password and invalidate tokens.
        await self._update_password_and_invalidate(user, new_password)

        # Log password change.
        await self.audit_repository.log_user_action_safe(
            action=AuditAction.PASSWORD_CHANGE,
            user_id=user.id,
            tenant_id=user.tenant_id or SYSTEM_TENANT_ID,
            email=user.email,
        )

        logger.info("password_changed", user_id=str(user.id))

    async def verify_user(self, user_id: UUID) -> None:
        """Mark user as verified.

        Args:
            user_id: User to verify.

        Raises:
            UserNotFoundError: If the user no longer exists.
        """
        user = await self.user_repository.set_verified(user_id)
        if not user:
            raise UserNotFoundError(f"User {user_id!s} not found.")
        logger.info("user_verified", user_id=str(user_id))

    async def send_verification_email(self, user: User) -> None:
        """Queue an email verification message for the given user.

        Generates a single-use Redis-backed token, builds the
        verification URL, and dispatches a Celery email task.
        The token expires after 24 hours.

        NOTE: Sending verification emails is rate-limited by the
        general user rate limiter. Projects may want to add an
        additional cooldown window (e.g. 60s) between sends.

        Args:
            user: User whose email address should be verified.
        """
        from app.core.security import create_email_verification_token

        await self._send_token_email(
            user=user,
            token_factory=create_email_verification_token(user.id),
            url_path="/auth/verify-email",
            subject=f"Verify your email — {settings.PROJECT_NAME}",
            template_name="verification.html",
            url_field_name="verification_url",
        )
        logger.info("verification_email_queued", user_id=str(user.id))

    async def send_password_reset_email(self, email: str) -> None:
        """Queue a password reset email if the account exists.

        Silently does nothing when the email is not registered to
        prevent user enumeration — callers should always return 204.
        Generates a single-use Redis-backed token with a 1-hour TTL.

        Args:
            email: Email address of the account to reset.
        """
        from app.core.security import create_password_reset_token

        user = await self.user_repository.get_by_email(email)
        if not user:
            # Do not reveal whether the account exists.
            return

        await self._send_token_email(
            user=user,
            token_factory=create_password_reset_token(user.id),
            url_path="/auth/reset-password",
            subject=f"Reset your password — {settings.PROJECT_NAME}",
            template_name="password_reset.html",
            url_field_name="reset_url",
        )
        logger.info("password_reset_email_queued", user_id=str(user.id))

    async def reset_password(self, token: str, new_password: str) -> None:
        """Reset a user's password using a valid reset token.

        Validates and consumes the single-use token, then updates
        the hashed password and increments `token_version` to
        invalidate all outstanding JWT tokens.

        Args:
            token: Opaque reset token from the password reset email.
            new_password: New plain-text password (min 8 characters).

        Raises:
            AuthenticationError: If the token is invalid or expired.
            UserNotFoundError: If the token's user no longer exists.
        """
        from app.core.security import verify_password_reset_token

        user_id = await verify_password_reset_token(token)
        if not user_id:
            raise AuthenticationError("Invalid or expired password reset token.")

        user = await self.user_repository.get(user_id)
        if not user:
            raise UserNotFoundError("User not found.")

        self._validate_password_strength(new_password)
        await self._update_password_and_invalidate(user, new_password)

        logger.info("password_reset", user_id=str(user.id))

    async def get_google_auth_url(self, redirect_uri: str) -> str:
        """Build and return the Google OAuth 2.0 authorization URL.

        Generates a single-use CSRF state token stored in Redis for
        10 minutes, then constructs the Google consent-screen URL.
        The caller must redirect the end-user to this URL.

        Args:
            redirect_uri: URL Google should redirect the user to after
                consent. Must match a URI registered in Google Console.

        Returns:
            Full Google OAuth authorization URL with state embedded.

        Raises:
            AuthenticationError: If Google OAuth is not configured.
        """
        from urllib.parse import urlencode

        from app.core.security import create_google_oauth_state

        if not settings.GOOGLE_CLIENT_ID:
            raise AuthenticationError("Google OAuth is not configured.")

        state = await create_google_oauth_state(redirect_uri)
        parameters = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "online",
        }
        return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(parameters)}"

    async def google_login(
        self, code: str, state: str, redirect_uri: str
    ) -> LoginResponse:
        """Authenticate or register a user via Google OAuth.

        Validates the CSRF state token, exchanges the authorization code
        for Google tokens, fetches the user's profile, then finds or
        creates a local account linked via `OAuthAccount`. Existing
        password-only accounts are linked by email on first Google login.

        Args:
            code: Authorization code received from Google callback.
            state: CSRF state token from the authorization URL.
            redirect_uri: Must exactly match the value used in the
                authorization request.

        Returns:
            LoginResponse with tokens, or a 2FA challenge if the linked
            account has 2FA enabled.

        Raises:
            AuthenticationError: If state is invalid, Google token
                exchange fails, or the user is inactive.
        """
        import httpx

        from app.core.security import verify_google_oauth_state
        from app.models.oauth_account import OAuthProvider

        expected_redirect_uri = await verify_google_oauth_state(state)
        if not expected_redirect_uri:
            raise AuthenticationError("Invalid or expired OAuth state.")
        if redirect_uri != expected_redirect_uri:
            raise AuthenticationError("OAuth redirect URI mismatch.")

        # Exchange authorization code for Google tokens.
        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )

        if token_response.status_code != 200:
            logger.warning(
                "google_token_exchange_failed",
                status=token_response.status_code,
            )
            raise AuthenticationError("Google token exchange failed.")

        access_token = token_response.json().get("access_token")
        if not access_token:
            raise AuthenticationError("No access token in Google response.")

        # Fetch user profile from Google.
        async with httpx.AsyncClient() as client:
            userinfo_response = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if userinfo_response.status_code != 200:
            raise AuthenticationError("Failed to fetch Google user profile.")

        userinfo = userinfo_response.json()

        # Reject accounts that have not verified their email with Google.
        if not userinfo.get("verified_email", False):
            raise AuthenticationError("Google account email is not verified.")

        google_id: str | None = userinfo.get("id")
        email: str | None = userinfo.get("email")
        full_name: str | None = userinfo.get("name")

        if not google_id or not email:
            raise AuthenticationError("Invalid Google userinfo response")

        # Find existing linked account by provider subject ID.
        oauth_account = await self.oauth_account_repository.get_by_provider(
            OAuthProvider.GOOGLE, google_id
        )

        if oauth_account:
            user: User | None = oauth_account.user
        else:
            user = await self.user_repository.get_by_email(email)
            if user:
                # Link Google to an existing password-based account.
                await self.oauth_account_repository.create_link(
                    user_id=user.id,
                    provider=OAuthProvider.GOOGLE,
                    provider_user_id=google_id,
                    provider_email=email,
                    tenant_id=user.tenant_id,
                )
                logger.info("google_account_linked", user_id=str(user.id))
            else:
                # Register a new account for first-time Google login.
                # NOTE: OAuth users are created without a tenant_id.
                # Projects requiring multi-tenancy should assign a tenant
                # during OAuth registration (e.g. via invitation or subdomain).
                placeholder_hash = await asyncio.to_thread(
                    generate_password_hash, secrets.token_urlsafe(32)
                )
                user = await self.user_repository.create_user(
                    email=email,
                    password_hash=placeholder_hash,
                    full_name=full_name,
                    role=UserRole.USER,
                    is_verified=True,
                )
                await self.oauth_account_repository.create_link(
                    user_id=user.id,
                    provider=OAuthProvider.GOOGLE,
                    provider_user_id=google_id,
                    provider_email=email,
                    tenant_id=user.tenant_id,
                )
                logger.info("google_user_created", user_id=str(user.id))

        if not user or not user.is_active:
            raise UserInactiveError("User account is inactive.")

        logger.info("google_login_success", user_id=str(user.id))
        return await self.issue_login_response(user)

    async def send_magic_link(self, email: str) -> None:
        """Queue a passwordless login link email if the account exists.

        Silently does nothing when the email is not registered to
        prevent user enumeration — callers should always return 204.

        Args:
            email: Email address of the account to send the link to.
        """
        from app.core.security import create_magic_link_token

        user = await self.user_repository.get_by_email(email)
        if not user:
            return

        await self._send_token_email(
            user=user,
            token_factory=create_magic_link_token(user.id),
            url_path="/auth/magic-link",
            subject=f"Sign in to {settings.PROJECT_NAME}",
            template_name="magic_link.html",
            url_field_name="login_url",
        )
        logger.info("magic_link_sent", user_id=str(user.id))

    async def verify_magic_link(self, token: str) -> LoginResponse:
        """Verify a magic link token and issue JWT tokens.

        Validates and consumes the single-use token, then returns
        a token pair for the authenticated user.

        Args:
            token: Opaque magic link token from the email.

        Returns:
            LoginResponse with tokens, or a 2FA challenge if the
            account has 2FA enabled.

        Raises:
            AuthenticationError: If the token is invalid or expired.
        """
        from app.core.security import verify_magic_link_token

        user_id = await verify_magic_link_token(token)
        if not user_id:
            raise AuthenticationError("Invalid or expired magic link.")

        user = await self.user_repository.get(user_id)
        if not user:
            raise AuthenticationError("Invalid or expired magic link.")
        if not user.is_active:
            raise UserInactiveError("User account is inactive.")

        logger.info("magic_link_verified", user_id=str(user.id))
        return await self.issue_login_response(user)
