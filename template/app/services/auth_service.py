"""Authentication service for user authentication.

This module provides the AuthService class for handling
user authentication, registration, and token management.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import DUMMY_PASSWORD_HASH
from app.core.security import create_access_token
from app.core.security import create_refresh_token
from app.core.security import decode_token
from app.core.security import generate_password_hash
from app.core.security import verify_password
from app.core.settings import settings
from app.models.audit_log import AuditAction
from app.models.user import User
from app.repositories.audit_repository import AuditLogRepository
from app.repositories.user_repository import UserRepository
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
        # Check if email exists.
        if await self.user_repository.email_exists(data.email):
            raise EmailAlreadyExistsError(f"Email {data.email} is already registered")

        # Create user.
        password_hash = generate_password_hash(data.password)
        user = await self.user_repository.create_user(
            email=data.email,
            password_hash=password_hash,
            full_name=data.full_name,
            tenant_id=tenant_id,
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
            verify_password(password, DUMMY_PASSWORD_HASH)
            raise InvalidCredentialsError("Invalid email or password")

        # Verify password.
        if not verify_password(password, user.hashed_password):
            raise InvalidCredentialsError("Invalid email or password")

        # Check if active.
        if not user.is_active:
            raise UserInactiveError("User account is inactive")

        # Log successful login.
        if user.tenant_id:
            await self.audit_repository.log_user_action(
                action=AuditAction.LOGIN,
                user_id=user.id,
                tenant_id=user.tenant_id,
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
                "is_superuser": user.is_superuser,
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

            return self.create_tokens(user)

        except Exception as e:
            logger.warning("token_refresh_failed", error=str(e))
            raise AuthenticationError("Invalid refresh token")

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
        # Verify current password.
        if not verify_password(current_password, user.hashed_password):
            raise InvalidCredentialsError("Current password is incorrect")

        # Update password.
        new_hash = generate_password_hash(new_password)
        await self.user_repository.update_password(user.id, new_hash)
        await self.user_repository.increment_token_version(user.id)

        # Log password change.
        if user.tenant_id:
            await self.audit_repository.log_user_action(
                action=AuditAction.PASSWORD_CHANGE,
                user_id=user.id,
                tenant_id=user.tenant_id,
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

        Args:
            user: User whose email address should be verified.
        """
        from app.core.security import create_email_verification_token
        from app.tasks.email import send_template_email_task

        token = await create_email_verification_token(user.id)
        verification_url = f"{settings.FRONTEND_HOST}/auth/verify-email?token={token}"

        send_template_email_task.delay(
            to_email=user.email,
            subject=f"Verify your email — {settings.PROJECT_NAME}",
            template_name="verification.html",
            context={
                "name": user.full_name or user.email,
                "verification_url": verification_url,
                "project": settings.PROJECT_NAME,
            },
        )

        logger.info("verification_email_queued", user_id=str(user.id))
