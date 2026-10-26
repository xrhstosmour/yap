"""Authentication service for user authentication.

This module provides the AuthService class for handling
user authentication, registration, and token management.
"""

from __future__ import annotations

import asyncio
from datetime import UTC
from datetime import datetime
from typing import Any
from uuid import UUID

from jwt import ExpiredSignatureError
from jwt import InvalidTokenError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import SYSTEM_TENANT_ID
from app.core.encryption import crypto
from app.core.logging import get_logger
from app.core.security import DUMMY_PASSWORD_HASH
from app.core.security import TokenRateLimitError
from app.core.security import blacklist_token
from app.core.security import claim_token_for_rotation
from app.core.security import create_access_token
from app.core.security import create_refresh_token
from app.core.security import decode_token
from app.core.security import generate_password_hash
from app.core.security import generate_unusable_password_hash
from app.core.security import is_token_blacklisted
from app.core.security import purge_user_tokens
from app.core.security import verify_password
from app.core.settings import settings
from app.core.tenant import system_context
from app.models.audit_log import AuditAction
from app.models.consent_record import MINIMUM_AGE_KIND
from app.models.user import User
from app.models.user import UserRole
from app.repositories.api_key_repository import APIKeyRepository
from app.repositories.audit_repository import AuditLogRepository
from app.repositories.consent_record_repository import ConsentRecordRepository
from app.repositories.oauth_account_repository import OAuthAccountRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import LoginResponse
from app.schemas.auth import RegisterRequest
from app.schemas.auth import TokenResponse
from app.services.two_factor_service import TwoFactorAuthService
from app.services.webauthn_service import WebAuthnService

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


class UsernameAlreadyExistsError(AuthenticationError):
    """The requested handle is taken."""

    pass


class MinimumAgeNotConfirmedError(AuthenticationError):
    """Registration did not confirm the person is at least `MINIMUM_AGE`."""

    pass


class UserNotFoundError(AuthenticationError):
    """User not found."""

    pass


class UserInactiveError(AuthenticationError):
    """User account is inactive."""

    pass


class OAuthProviderUnavailableError(AuthenticationError):
    """Social sign-in is not configured, or the provider cannot be reached."""

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

        Template implementation, projects should customize this
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

    @staticmethod
    async def _claim_payload_token(payload: dict[str, Any]) -> bool:
        """Atomically blacklist a decoded token, reporting whether we won.

        Args:
            payload: Decoded JWT payload.

        Returns:
            True when this caller claimed the token, False when it had
            already been used. A payload without usable claims cannot be
            claimed and is treated as won, matching the previous
            behaviour of `_blacklist_payload_token`.
        """
        token_identifier = payload.get("jti")
        token_expiration = payload.get("exp")

        if not isinstance(token_identifier, str):
            return True
        if not isinstance(token_expiration, (int, float)):
            return True

        expires_at = datetime.fromtimestamp(token_expiration, tz=UTC)
        return await claim_token_for_rotation(token_identifier, expires_at)

    async def _update_password_and_invalidate(
        self, user: User, new_password: str
    ) -> None:
        """Hash a new password and invalidate all outstanding tokens.

        Sessions are invalidated through `token_version`, and the reset,
        magic link and verification links mailed earlier are revoked, so
        none of them outlives the credential it was issued against.

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
        await purge_user_tokens(user.id)

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
        # or .apply_async(), this is correct even in an async context because
        # the task is serialized and handed off to the broker, not executed inline.
        try:
            send_template_email_task.delay(
                to_email=user.email,
                subject=subject,
                template_name=template_name,
                context={
                    "name": user.full_name or user.email,
                    url_field_name: url,
                    "project": settings.PRODUCT_NAME,
                },
            )
        except Exception:
            logger.warning(
                "email_task_dispatch_failed",
                user_id=str(user.id),
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
            UsernameAlreadyExistsError: If the requested handle is taken
        """
        self._validate_password_strength(data.password)

        # Refused before anything is looked up, so a child's address is
        # never so much as compared against the table.
        if settings.MINIMUM_AGE and not data.is_minimum_age_confirmed:
            message = f"You need to be at least {settings.MINIMUM_AGE} to sign up"
            raise MinimumAgeNotConfirmedError(message)

        # Check if email exists.
        if await self.user_repository.email_exists(data.email):
            raise EmailAlreadyExistsError("Email already registered")

        # Claim the handle before creating anything, so a taken one fails
        # the request rather than leaving an account with no handle behind.
        if data.username and await self.user_repository.get_by_username(data.username):
            raise UsernameAlreadyExistsError("That username is taken")

        # Create user.
        password_hash = await asyncio.to_thread(generate_password_hash, data.password)
        user = await self.user_repository.create_user(
            email=data.email,
            password_hash=password_hash,
            full_name=data.full_name,
            tenant_id=tenant_id,
            role=UserRole.USER,
            username=data.username,
        )

        if settings.MINIMUM_AGE:
            await ConsentRecordRepository(self.session).record(
                user.id,
                MINIMUM_AGE_KIND,
                is_granted=True,
                document_version=str(settings.MINIMUM_AGE),
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
            # No account, and so no tenant, exists for this email: falls
            # back to SYSTEM_TENANT_ID, the same well-known tenant used
            # elsewhere in this service for actors without one yet (see
            # verify_magic_link and google_login).
            await self.audit_repository.log_user_action_safe(
                action=AuditAction.LOGIN_FAILED,
                user_id="unknown",
                tenant_id=SYSTEM_TENANT_ID,
                email=email,
                status="failure",
            )
            # `log_user_action_safe` only writes inside a SAVEPOINT; the
            # session-scoped commit that normally follows a route never
            # runs here because this method raises immediately below,
            # which rolls the whole transaction (savepoint included) back
            # instead. Commit explicitly so the audit trail this is for
            # actually survives the failed request.
            await self.session.commit()
            raise InvalidCredentialsError("Invalid email or password")

        # Verify password.
        if not await asyncio.to_thread(verify_password, password, user.hashed_password):
            await self.audit_repository.log_user_action_safe(
                action=AuditAction.LOGIN_FAILED,
                user_id=user.id,
                tenant_id=user.tenant_id or SYSTEM_TENANT_ID,
                email=user.email,
                status="failure",
            )
            await self.session.commit()
            raise InvalidCredentialsError("Invalid email or password")

        # Check if active.
        if not user.is_active:
            await self.audit_repository.log_user_action_safe(
                action=AuditAction.LOGIN_FAILED,
                user_id=user.id,
                tenant_id=user.tenant_id or SYSTEM_TENANT_ID,
                email=user.email,
                status="failure",
                error_message="inactive",
            )
            await self.session.commit()
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

    def create_tokens(
        self, user: User, refreshed_from: dict[str, Any] | None = None
    ) -> TokenResponse:
        """Create access and refresh tokens for user.

        Both tokens carry `auth_time`, when the person last proved who they
        are. A sign in stamps it with now, a refresh passes the payload it
        exchanged and keeps that value as it was, so refreshing never makes
        a session look freshly signed in. A refresh token minted before the
        claim existed carries none, and its descendants carry none.

        Args:
            user: Authenticated user
            refreshed_from: Decoded payload of the refresh token being
                exchanged, `None` for a sign in.

        Returns:
            TokenResponse with access and refresh tokens
        """
        auth_time = (
            int(datetime.now(UTC).timestamp())
            if refreshed_from is None
            else refreshed_from.get("auth_time")
        )
        auth_time_claim = {} if auth_time is None else {"auth_time": auth_time}

        # Access token.
        access_token = create_access_token(
            subject=str(user.id),
            additional_claims={
                "email": user.email,
                "tenant_id": str(user.tenant_id) if user.tenant_id else None,
                "role": user.role.value,
                "token_version": user.token_version,
                **auth_time_claim,
            },
        )

        # Refresh token.
        refresh_token = create_refresh_token(
            subject=str(user.id),
            additional_claims={
                "token_version": user.token_version,
                **auth_time_claim,
            },
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

            # Bootstrapping identity from a signed token's user ID: the
            # tenant isn't known yet, that's what this lookup establishes.
            with system_context():
                user = await self.user_repository.get(UUID(user_id))
            if not user:
                raise UserNotFoundError("User not found")

            if not user.is_active:
                raise UserInactiveError("User account is inactive")

            token_version = payload.get("token_version")
            if token_version is not None and token_version != user.token_version:
                raise AuthenticationError("Token invalidated due to password change")

            # Claim the old token before minting its replacement, in one
            # atomic operation. Checking the blacklist above and writing to
            # it afterwards left a window where two concurrent refreshes
            # both passed the check and both got a fresh pair, so a stolen
            # token replayed alongside the real one succeeded and the
            # blacklist caught nothing.
            #
            # Placed after every read-only validation so a rejected refresh
            # does not burn a token that was never exchanged.
            #
            # Only the refresh token is blacklisted. Access tokens are
            # short-lived and expire naturally; blacklisting them adds
            # Redis overhead with minimal security benefit.
            if not await self._claim_payload_token(payload):
                raise AuthenticationError("Refresh token has been revoked")

            return self.create_tokens(user, refreshed_from=payload)

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

    async def verify_user(self, user_id: UUID, email_hash: str) -> None:
        """Mark user as verified.

        The link proves the address it was mailed to, so it only counts
        while that is still the account's address.

        Args:
            user_id: User to verify.
            email_hash: Search hash of the address the token was issued for.

        Raises:
            UserNotFoundError: If the user no longer exists.
            AuthenticationError: If the account's email changed since the
                token was issued.
        """
        # Bootstrapping identity from a verification token: the tenant
        # isn't known yet, that's what this lookup establishes.
        with system_context():
            user = await self.user_repository.get(user_id)
            if not user:
                raise UserNotFoundError(f"User {user_id!s} not found.")
            if email_hash not in crypto.hash_candidates_for_search(user.email):
                raise AuthenticationError("Verification token is for another email.")
            await self.user_repository.set_verified(user_id)
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
            token_factory=create_email_verification_token(user.id, user.email),
            url_path="/auth/verify-email",
            subject=f"Verify your email, {settings.PRODUCT_NAME}",
            template_name="verification.html",
            url_field_name="verification_url",
        )
        logger.info("verification_email_queued", user_id=str(user.id))

    async def send_password_reset_email(self, email: str) -> None:
        """Queue a password reset email if the account exists.

        Silently does nothing when the email is not registered to
        prevent user enumeration, callers should always return 204.
        Generates a single-use Redis-backed token with a 1-hour TTL.

        The per-user cooldown is swallowed for the same reason. It only
        fires for addresses that exist, so letting it surface turned the
        endpoint into an enumeration oracle: submit an address twice and a
        429 on the second attempt meant the account was real, while two
        204s meant it was not. The cooldown still does its job, no second
        email goes out, it just stops being observable from outside.

        Args:
            email: Email address of the account to reset.
        """
        from app.core.security import create_password_reset_token

        user = await self.user_repository.get_by_email(email)
        if not user:
            # Do not reveal whether the account exists.
            return

        try:
            await self._send_token_email(
                user=user,
                token_factory=create_password_reset_token(user.id),
                url_path="/auth/reset-password",
                subject=f"Reset your password, {settings.PRODUCT_NAME}",
                template_name="password_reset.html",
                url_field_name="reset_url",
            )
        except TokenRateLimitError:
            logger.info("password_reset_email_throttled", user_id=str(user.id))
            return

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

        # Checked before the token is consumed, a weak password must not
        # burn the only link the person has.
        self._validate_password_strength(new_password)

        user_id = await verify_password_reset_token(token)
        if not user_id:
            raise AuthenticationError("Invalid or expired password reset token.")

        # Bootstrapping identity from a password-reset token: the tenant
        # isn't known yet, that's what this lookup establishes. The update
        # right after needs the same context, both stay inside the block.
        with system_context():
            user = await self.user_repository.get(user_id)
            if not user:
                raise UserNotFoundError("User not found.")

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

        return await self._login_with_oauth_identity(
            OAuthProvider.GOOGLE, google_id, email, full_name
        )

    async def _login_with_oauth_identity(
        self,
        provider: str,
        provider_user_id: str,
        email: str | None,
        full_name: str | None,
    ) -> LoginResponse:
        """Find or create the account behind a verified provider identity.

        The provider has already proven the identity, this only maps it
        to a local account: the linked one, else the one with the same
        email, linked on the way, else a new verified one.

        Args:
            provider: `OAuthProvider` value of the identity.
            provider_user_id: The provider's stable subject ID.
            email: Verified email from the provider, `None` when it sent
                none, which only works for an identity already linked.
            full_name: Display name for a new account, if the provider
                gave one.

        Returns:
            LoginResponse with tokens, or a 2FA challenge if the account
            has 2FA enabled.

        Raises:
            AuthenticationError: If the identity is unlinked and has no
                email to link or register with.
            UserInactiveError: If the account is inactive.
        """
        # Find existing linked account by provider subject ID.
        oauth_account = await self.oauth_account_repository.get_by_provider(
            provider, provider_user_id
        )

        if oauth_account:
            user: User | None = oauth_account.user
        elif not email:
            raise AuthenticationError(f"No email in the {provider} identity.")
        else:
            user = await self.user_repository.get_by_email(email)
            if user:
                # The provider proved the address, the password account may
                # not have. Whoever registered it could be someone else.
                if not user.is_verified:
                    await self._neutralize_unverified_account(user)

                # Link the provider to an existing password-based account.
                await self.oauth_account_repository.create_link(
                    user_id=user.id,
                    provider=provider,
                    provider_user_id=provider_user_id,
                    provider_email=email,
                    tenant_id=user.tenant_id,
                )
                logger.info(f"{provider}_account_linked", user_id=str(user.id))
            else:
                # Register a new account for a first-time provider login.
                # NOTE: OAuth users are created without a tenant_id.
                # Projects requiring multi-tenancy should assign a tenant
                # during OAuth registration (e.g. via invitation or subdomain).
                user = await self.user_repository.create_user(
                    email=email,
                    password_hash=generate_unusable_password_hash(),
                    full_name=full_name,
                    role=UserRole.USER,
                    is_verified=True,
                )
                await self.oauth_account_repository.create_link(
                    user_id=user.id,
                    provider=provider,
                    provider_user_id=provider_user_id,
                    provider_email=email,
                    tenant_id=user.tenant_id,
                )
                logger.info(f"{provider}_user_created", user_id=str(user.id))

        if not user or not user.is_active:
            raise UserInactiveError("User account is inactive.")

        logger.info(f"{provider}_login_success", user_id=str(user.id))
        return await self.issue_login_response(user)

    async def _neutralize_unverified_account(self, user: User) -> None:
        """Cut off whoever registered an account before its email was proven.

        `register` hands out tokens for an unverified account, so a stranger
        can claim an address before its owner shows up. When the owner then
        signs in with a provider that has proven the address, the account is
        theirs from that moment: the password is replaced by one nobody can
        match, every session and mailed link is revoked, and the address is
        marked verified.

        Args:
            user: The unverified account a provider identity is linking to.
        """
        # A provider sign in has no tenant yet, the update needs one.
        with system_context():
            await self.user_repository.update(
                user.id,
                {
                    "hashed_password": generate_unusable_password_hash(),
                    "token_version": User.token_version + 1,
                    "is_verified": True,
                },
            )
            await self._clear_account_access(user.id)
        await purge_user_tokens(user.id)
        logger.info("unverified_account_neutralized", user_id=str(user.id))

    async def _clear_account_access(
        self, user_id: UUID, *, second_factor: bool = True
    ) -> None:
        """Remove every credential besides the password someone may have added.

        Passkeys, the TOTP secret with its recovery codes, and API keys all
        sign in without the password or outlive a `token_version` bump, so
        taking an account back means removing them too. Runs in the caller's
        transaction and tenant context.

        Args:
            user_id: The account being taken back.
            second_factor: Whether to remove the TOTP secret and recovery
                codes too. Keep them when the account's owner may well have
                set them up, since removing them would let whoever reaches
                the mailbox skip the second factor.
        """
        await WebAuthnService(self.session).delete_for_user(user_id)
        if second_factor:
            await TwoFactorAuthService(self.session).clear_enrollment(user_id)
        await APIKeyRepository(self.session).revoke_all_for_user(user_id)

    async def apple_native_login(
        self,
        identity_token: str,
        nonce: str,
        full_name: str | None = None,
        authorization_code: str | None = None,
    ) -> LoginResponse:
        """Authenticate or register a user from a native Sign in with Apple.

        The app runs the system sheet itself and sends the identity token
        Apple gave it, which alone proves the identity. The authorization
        code that came with it, when sent and a key is configured, is
        exchanged for the refresh token account deletion revokes. A failed
        exchange is logged and does not stop the sign in.

        Args:
            identity_token: The identity token from the system sheet.
            nonce: The raw nonce whose SHA-256 the app sent to Apple.
            full_name: The name Apple handed the app, only ever on the
                first authorization, so it is only used for a new account.
            authorization_code: The code from the system sheet, if sent.

        Returns:
            LoginResponse with tokens, or a 2FA challenge if the account
            has 2FA enabled.

        Raises:
            OAuthProviderUnavailableError: If `APPLE_BUNDLE_ID` is unset or
                Apple's keys cannot be fetched.
            AuthenticationError: If the token does not verify.
            UserInactiveError: If the account is inactive.
        """
        if not settings.APPLE_BUNDLE_ID:
            raise OAuthProviderUnavailableError("Sign in with Apple is not configured.")

        claims = await self._verify_apple_identity_token(
            identity_token, settings.APPLE_BUNDLE_ID, nonce
        )
        refresh_token = None
        if authorization_code and settings.APPLE_PRIVATE_KEY:
            refresh_token = await self._apple_native_refresh_token(
                authorization_code, claims["sub"]
            )
        return await self._login_with_apple_claims(
            claims, full_name, refresh_token, settings.APPLE_BUNDLE_ID
        )

    async def _apple_native_refresh_token(
        self, authorization_code: str, subject: str
    ) -> str | None:
        """Exchange an app's authorization code for its refresh token.

        Only a token for the same Apple account as the verified identity
        token is kept, so nobody can attach another person's authorization
        to their account, and have it revoked with theirs.

        Returns:
            The refresh token, or `None` if the exchange failed or named
            another account.
        """
        try:
            body = await self._exchange_apple_code(
                authorization_code, settings.APPLE_BUNDLE_ID
            )
            claims = await self._verify_apple_identity_token(
                body.get("id_token") or "", settings.APPLE_BUNDLE_ID
            )
        except (AuthenticationError, OAuthProviderUnavailableError) as exc:
            logger.warning("apple_native_code_exchange_failed", reason=str(exc))
            return None
        if claims.get("sub") != subject:
            logger.warning("apple_native_code_for_another_account")
            return None
        refresh_token: str | None = body.get("refresh_token")
        return refresh_token

    async def get_apple_auth_url(self, redirect_uri: str) -> str:
        """Build the Sign in with Apple authorization URL for the web flow.

        Apple only sends the name and email to a `form_post` callback,
        so `redirect_uri` has to accept a form POST and hand the code and
        state on to `POST /auth/apple/callback`.

        Args:
            redirect_uri: URL Apple posts the result to, registered on the
                Services ID.

        Returns:
            Full Apple authorization URL with state embedded.

        Raises:
            OAuthProviderUnavailableError: If the web flow is not configured.
        """
        from urllib.parse import urlencode

        from app.core.security import create_apple_oauth_state

        if not settings.APPLE_CLIENT_ID or not settings.APPLE_PRIVATE_KEY:
            raise OAuthProviderUnavailableError("Sign in with Apple is not configured.")

        state = await create_apple_oauth_state(redirect_uri)
        parameters = {
            "client_id": settings.APPLE_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "response_mode": "form_post",
            "scope": "name email",
            "state": state,
        }
        return f"https://appleid.apple.com/auth/authorize?{urlencode(parameters)}"

    async def apple_login(
        self,
        code: str,
        state: str,
        redirect_uri: str,
        full_name: str | None = None,
    ) -> LoginResponse:
        """Authenticate or register a user via the Apple web flow.

        Validates the CSRF state, exchanges the code with a signed client
        secret, verifies the identity token Apple returns, then finds or
        creates the account as `google_login` does.

        Args:
            code: Authorization code Apple posted to the callback.
            state: CSRF state token from the authorization URL.
            redirect_uri: Must exactly match the authorization request.
            full_name: The name Apple posted, first authorization only.

        Returns:
            LoginResponse with tokens, or a 2FA challenge if the account
            has 2FA enabled.

        Raises:
            OAuthProviderUnavailableError: If the web flow is not configured
                or Apple cannot be reached.
            AuthenticationError: If the state, code or token is invalid.
            UserInactiveError: If the account is inactive.
        """
        from app.core.security import verify_apple_oauth_state

        if not settings.APPLE_CLIENT_ID or not settings.APPLE_PRIVATE_KEY:
            raise OAuthProviderUnavailableError("Sign in with Apple is not configured.")

        expected_redirect_uri = await verify_apple_oauth_state(state)
        if not expected_redirect_uri:
            raise AuthenticationError("Invalid or expired OAuth state.")
        if redirect_uri != expected_redirect_uri:
            raise AuthenticationError("OAuth redirect URI mismatch.")

        body = await self._exchange_apple_code(
            code, settings.APPLE_CLIENT_ID, redirect_uri
        )
        identity_token = body.get("id_token")
        if not identity_token:
            raise AuthenticationError("No identity token in Apple response.")

        claims = await self._verify_apple_identity_token(
            identity_token, settings.APPLE_CLIENT_ID
        )
        return await self._login_with_apple_claims(
            claims, full_name, body.get("refresh_token"), settings.APPLE_CLIENT_ID
        )

    async def _exchange_apple_code(
        self, code: str, client_id: str, redirect_uri: str | None = None
    ) -> dict[str, Any]:
        """Exchange an Apple authorization code at Apple's token endpoint.

        Args:
            code: The authorization code.
            client_id: The client the code was issued to, the Services ID
                for the web flow, the bundle ID for the app.
            redirect_uri: The web flow's redirect URI, which Apple checks,
                `None` for a code from the app.

        Returns:
            Apple's response, with `id_token` and `refresh_token`.

        Raises:
            OAuthProviderUnavailableError: If Apple cannot be reached.
            AuthenticationError: If Apple refuses the code.
        """
        import httpx

        from app.core.security import APPLE_ISSUER
        from app.core.security import create_apple_client_secret

        data = {
            "client_id": client_id,
            "client_secret": create_apple_client_secret(client_id),
            "code": code,
            "grant_type": "authorization_code",
        }
        if redirect_uri:
            data["redirect_uri"] = redirect_uri
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                token_response = await client.post(
                    f"{APPLE_ISSUER}/auth/token", data=data
                )
        except httpx.HTTPError as exc:
            logger.warning("apple_token_exchange_unreachable", error=str(exc))
            raise OAuthProviderUnavailableError("Apple cannot be reached.") from exc

        if token_response.status_code != 200:
            logger.warning(
                "apple_token_exchange_failed",
                status=token_response.status_code,
            )
            raise AuthenticationError("Apple token exchange failed.")
        body: dict[str, Any] = token_response.json()
        return body

    async def _verify_apple_identity_token(
        self, identity_token: str, audience: str, nonce: str | None = None
    ) -> dict[str, Any]:
        """Verify an Apple identity token, mapping failures to service errors."""
        import httpx

        from app.core.security import verify_apple_identity_token

        try:
            return await verify_apple_identity_token(identity_token, audience, nonce)
        except httpx.HTTPError as exc:
            logger.warning("apple_keys_unreachable", error=str(exc))
            raise OAuthProviderUnavailableError("Apple cannot be reached.") from exc
        except InvalidTokenError as exc:
            logger.info("apple_identity_token_rejected", reason=str(exc))
            raise AuthenticationError("Invalid Apple identity token.") from exc

    async def _login_with_apple_claims(
        self,
        claims: dict[str, Any],
        full_name: str | None,
        refresh_token: str | None = None,
        client_id: str | None = None,
    ) -> LoginResponse:
        """Sign in the account behind verified Apple identity token claims.

        A refresh token, when there is one, replaces the one kept on the
        link, the newest authorization being the one to revoke.
        """
        from app.models.oauth_account import OAuthProvider

        email: str | None = claims.get("email")
        # Apple sends the flag as a boolean or as the string "true".
        if email and str(claims.get("email_verified")).lower() != "true":
            raise AuthenticationError("Apple account email is not verified.")

        response = await self._login_with_oauth_identity(
            OAuthProvider.APPLE, claims["sub"], email, full_name
        )
        if refresh_token:
            account = await self.oauth_account_repository.get_by_provider(
                OAuthProvider.APPLE, claims["sub"]
            )
            if account:
                # Set on the loaded row, the repository's `update` wants a
                # tenant context a sign in does not have yet.
                account.refresh_token = refresh_token
                account.refresh_token_client_id = client_id
                await self.session.flush()
        return response

    async def send_magic_link(self, email: str) -> None:
        """Queue a passwordless login link email if the account exists.

        Silently does nothing when the email is not registered to
        prevent user enumeration, callers should always return 204. The
        per-user cooldown is swallowed for the same reason, see
        ``send_password_reset_email``.

        Args:
            email: Email address of the account to send the link to.
        """
        from app.core.security import create_magic_link_token

        user = await self.user_repository.get_by_email(email)
        if not user:
            return

        try:
            await self._send_token_email(
                user=user,
                token_factory=create_magic_link_token(user.id),
                url_path="/auth/magic-link",
                subject=f"Sign in to {settings.PRODUCT_NAME}",
                template_name="magic_link.html",
                url_field_name="login_url",
            )
        except TokenRateLimitError:
            logger.info("magic_link_throttled", user_id=str(user.id))
            return

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

        # Bootstrapping identity from a magic-link token: the tenant isn't
        # known yet, that's what this lookup establishes.
        with system_context():
            user = await self.user_repository.get(user_id)
        if not user:
            raise AuthenticationError("Invalid or expired magic link.")
        if not user.is_active:
            raise UserInactiveError("User account is inactive.")

        logger.info("magic_link_verified", user_id=str(user.id))
        return await self.issue_login_response(user)
