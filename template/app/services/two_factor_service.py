"""Two-factor authentication service using TOTP.

This module provides the TwoFactorAuthService for managing TOTP-based
2FA enrollment, verification, and recovery code lifecycle.
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import and_
from sqlmodel import select

from app.core.cache import get_redis
from app.core.encryption import decrypt
from app.core.encryption import encrypt
from app.core.logging import get_logger
from app.core.security import build_totp_provisioning_uri
from app.core.security import generate_password_hash
from app.core.security import generate_recovery_codes
from app.core.security import generate_totp_qr_data_url
from app.core.security import generate_totp_secret
from app.core.security import verify_password
from app.core.security import verify_totp
from app.core.settings import settings
from app.core.tenant import system_context
from app.models.totp_recovery_code import TotpRecoveryCode
from app.models.user import User
from app.repositories.user_repository import UserRepository

logger = get_logger("service.two_factor")


class TwoFactorError(Exception):
    """Base exception for 2FA errors."""

    pass


class InvalidTOTPError(TwoFactorError):
    """Invalid or expired TOTP code."""

    pass


def _validate_totp_code(code: str) -> str:
    """Validate and normalise a raw TOTP code from user input.

    Args:
        code: Raw input from the caller.

    Returns:
        Stripped 6-digit code string.

    Raises:
        InvalidTOTPError: If the code is not exactly 6 numeric digits.
    """
    stripped = code.strip() if isinstance(code, str) else ""
    if not stripped.isdigit() or len(stripped) != 6:
        raise InvalidTOTPError("TOTP code must be exactly 6 digits.")
    return stripped


class TwoFactorRateLimitError(TwoFactorError):
    """Too many failed TOTP attempts."""

    pass


class TwoFactorNotEnabledError(TwoFactorError):
    """2FA is not enabled for this user."""

    pass


class TwoFactorAlreadyEnabledError(TwoFactorError):
    """2FA is already enabled for this user."""

    pass


class TwoFactorAuthService:
    """Service for TOTP-based two-factor authentication.

    Handles enrollment, verification, challenge-response flow,
    recovery codes, and disabling 2FA. All TOTP secrets are
    encrypted at rest using Fernet (CRYPTO_KEY).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the 2FA service.

        Args:
            session: Async database session.
        """
        self.session = session
        self.user_repository = UserRepository(session)

    # Enrollment

    async def begin_enrollment(
        self,
        user: User,
    ) -> tuple[str, str, list[str]]:
        """Begin TOTP enrollment for a user.

        Generates a TOTP secret, encrypts it, stores it on the user,
        creates 10 hashed recovery codes, and returns the QR data URL
        and plaintext recovery codes (shown only once).

        Args:
            user: The user enabling 2FA.

        Returns:
            Tuple of (secret, qr_data_url, recovery_codes) where
            secret is the base32 TOTP secret, qr_data_url is a
            data:image/png;base64 string, and recovery_codes are
            the plaintext codes (must be shown to user immediately).

        Raises:
            TwoFactorAlreadyEnabledError: If 2FA is already active.
        """
        if user.is_2fa_enabled:
            raise TwoFactorAlreadyEnabledError("2FA is already enabled for this user.")

        # Generate and encrypt TOTP secret.
        secret = generate_totp_secret()
        encrypted_secret = encrypt(secret)

        # Build QR code.
        provisioning_uri = build_totp_provisioning_uri(user.email, secret)
        qr_data_url = generate_totp_qr_data_url(provisioning_uri)

        # Generate plaintext recovery codes and store hashes.
        recovery_codes = generate_recovery_codes(settings.TOTP_RECOVERY_CODE_COUNT)
        await self._replace_recovery_codes(user.id, recovery_codes)

        # Persist encrypted secret (2FA not yet active until confirmed).
        await self.user_repository.update(
            user.id,
            {
                "totp_secret_encrypted": encrypted_secret,
                "is_2fa_enabled": False,
                "totp_confirmed_at": None,
            },
        )

        logger.info("totp_enrollment_started", user_id=str(user.id))
        return secret, qr_data_url, recovery_codes

    async def confirm_enrollment(self, user: User, totp_code: str) -> None:
        """Confirm TOTP enrollment by verifying the first valid code.

        The user must provide a valid TOTP code from their authenticator
        app to activate 2FA. This prevents lockout if enrollment was
        interrupted before the QR code was scanned.

        Args:
            user: The user confirming enrollment.
            totp_code: 6-digit TOTP code from the authenticator app.

        Raises:
            TwoFactorError: If no pending enrollment exists.
            TwoFactorRateLimitError: If rate limit exceeded.
            InvalidTOTPError: If the TOTP code is invalid.
        """
        if not user.totp_secret_encrypted:
            raise TwoFactorError("No pending 2FA enrollment found.")

        if user.is_2fa_enabled:
            raise TwoFactorAlreadyEnabledError("2FA is already enabled.")

        await self._check_totp_rate_limit(user.id)

        totp_code = _validate_totp_code(totp_code)

        try:
            secret = decrypt(user.totp_secret_encrypted)
        except Exception as e:
            raise TwoFactorError("TOTP secret corrupted, please re-enroll") from e

        if not verify_totp(secret, totp_code):
            raise InvalidTOTPError("Invalid TOTP code.")

        # Activate 2FA and invalidate all existing sessions.
        await self.user_repository.update(
            user.id,
            {
                "is_2fa_enabled": True,
                "totp_confirmed_at": datetime.now(UTC),
                "token_version": User.token_version + 1,
            },
        )
        await self._reset_totp_rate_limit(user.id)

        logger.info("totp_enrollment_confirmed", user_id=str(user.id))

    # Challenge / Verification

    async def issue_challenge(self, user: User) -> str:
        """Issue a short-lived 2FA challenge token.

        Stores the challenge in Redis with a 5-minute TTL. The token
        is an opaque random string, not a JWT.

        Args:
            user: The authenticated user who needs to complete 2FA.

        Returns:
            Challenge token string (32-byte URL-safe random).
        """
        token = secrets.token_urlsafe(32)
        redis = await get_redis()
        await redis.setex(
            f"2fa:challenge:{token}",
            settings.TOTP_CHALLENGE_TTL_SECONDS,
            str(user.id),
        )
        logger.debug("totp_challenge_issued", user_id=str(user.id))
        return token

    async def verify_challenge(self, challenge_token: str, totp_code: str) -> User:
        """Verify a 2FA challenge with a TOTP code.

        Consumes the challenge token atomically (GETDEL), verifies the
        TOTP code, and prevents replay attacks via Redis.

        Args:
            challenge_token: Token issued by issue_challenge().
            totp_code: 6-digit TOTP code from the authenticator app.

        Returns:
            Authenticated User.

        Raises:
            InvalidTOTPError: If challenge token is expired/invalid or TOTP code wrong.
            TwoFactorRateLimitError: If rate limit exceeded.
        """
        user = await self._consume_challenge(challenge_token)

        await self._check_totp_rate_limit(user.id)

        totp_code = _validate_totp_code(totp_code)

        if not user.totp_secret_encrypted:
            raise TwoFactorError("User has no TOTP secret.")

        try:
            secret = decrypt(user.totp_secret_encrypted)
        except Exception as e:
            raise TwoFactorError("TOTP secret corrupted, please re-enroll") from e

        if not verify_totp(secret, totp_code):
            raise InvalidTOTPError("Invalid TOTP code.")

        await self._prevent_replay(user.id, totp_code)
        await self._reset_totp_rate_limit(user.id)

        logger.info("totp_challenge_verified", user_id=str(user.id))
        return user

    async def verify_challenge_with_recovery(
        self,
        challenge_token: str,
        recovery_code: str,
    ) -> User:
        """Verify a 2FA challenge using a backup recovery code.

        The recovery code is consumed (marked used) on successful verification.

        Args:
            challenge_token: Token issued by issue_challenge().
            recovery_code: Plaintext recovery code (XXXX-XXXX format).

        Returns:
            Authenticated User.

        Raises:
            InvalidTOTPError: If challenge or recovery code is invalid.
            TwoFactorRateLimitError: If rate limit exceeded.
        """
        user = await self._consume_challenge(challenge_token)

        await self._check_totp_rate_limit(user.id)

        # Find and consume a matching unused recovery code.
        # NOTE: TotpRecoveryCode does not have a tenant_id field, recovery
        # codes are tenant-agnostic. Projects requiring tenant isolation
        # should add a tenant_id column and join through the User model.
        #
        # `with_for_update()` locks the matching rows for the rest of this
        # transaction, so a second, concurrent request presenting the same
        # code cannot also read it as unused: it blocks until this
        # transaction commits, then re-reads `used_at` as already set and
        # finds nothing to consume. Without the lock, both requests could
        # verify the same code and both succeed, defeating single use.
        query = (
            select(TotpRecoveryCode)
            .where(
                and_(
                    TotpRecoveryCode.user_id == user.id,  # type: ignore[arg-type]
                    TotpRecoveryCode.used_at.is_(None),  # type: ignore[union-attr]
                )
            )
            .with_for_update()
        )
        result = await self.session.execute(query)
        unused_codes = result.scalars().all()

        for code_record in unused_codes:
            if await asyncio.to_thread(
                verify_password, recovery_code, code_record.code_hash
            ):
                statement = (
                    update(TotpRecoveryCode)
                    .where(
                        TotpRecoveryCode.id == code_record.id,  # type: ignore[arg-type]
                    )
                    .values(used_at=datetime.now(UTC))
                )
                await self.session.execute(statement)
                await self.session.flush()
                await self._reset_totp_rate_limit(user.id)
                logger.info(
                    "totp_recovery_code_used",
                    user_id=str(user.id),
                    code_id=str(code_record.id),
                )
                return user

        raise InvalidTOTPError("Invalid recovery code.")

    # Management

    async def disable(self, user: User, totp_code: str) -> None:
        """Disable 2FA for a user.

        Requires a valid TOTP code to prevent unauthorized disabling.
        Clears the secret, recovery codes, and bumps token_version.

        Args:
            user: The user disabling 2FA.
            totp_code: 6-digit TOTP code to confirm intent.

        Raises:
            TwoFactorNotEnabledError: If 2FA is not active.
            TwoFactorRateLimitError: If rate limit exceeded.
            InvalidTOTPError: If the TOTP code is invalid.
        """
        if not user.is_2fa_enabled:
            raise TwoFactorNotEnabledError("2FA is not enabled for this user.")

        await self._check_totp_rate_limit(user.id)

        totp_code = _validate_totp_code(totp_code)

        if not user.totp_secret_encrypted:
            raise TwoFactorError("User has no TOTP secret.")

        try:
            secret = decrypt(user.totp_secret_encrypted)
        except Exception as e:
            raise TwoFactorError("TOTP secret corrupted, please re-enroll") from e

        if not verify_totp(secret, totp_code):
            raise InvalidTOTPError("Invalid TOTP code.")

        await self.user_repository.update(
            user.id,
            {
                "is_2fa_enabled": False,
                "totp_secret_encrypted": None,
                "totp_confirmed_at": None,
                "token_version": User.token_version + 1,
            },
        )
        await self._delete_all_recovery_codes(user.id)
        await self._reset_totp_rate_limit(user.id)

        logger.info("totp_disabled", user_id=str(user.id))

    async def regenerate_recovery_codes(
        self,
        user: User,
        totp_code: str,
    ) -> list[str]:
        """Regenerate all recovery codes, requiring a valid TOTP code.

        Args:
            user: The user regenerating their recovery codes.
            totp_code: 6-digit TOTP code to confirm intent.

        Returns:
            List of new plaintext recovery codes (shown only once).

        Raises:
            TwoFactorNotEnabledError: If 2FA is not active.
            TwoFactorRateLimitError: If rate limit exceeded.
            InvalidTOTPError: If the TOTP code is invalid.
        """
        if not user.is_2fa_enabled:
            raise TwoFactorNotEnabledError("2FA is not enabled for this user.")

        await self._check_totp_rate_limit(user.id)

        totp_code = _validate_totp_code(totp_code)

        if not user.totp_secret_encrypted:
            raise TwoFactorError("User has no TOTP secret.")

        try:
            secret = decrypt(user.totp_secret_encrypted)
        except Exception as e:
            raise TwoFactorError("TOTP secret corrupted, please re-enroll") from e

        if not verify_totp(secret, totp_code):
            raise InvalidTOTPError("Invalid TOTP code.")

        new_codes = generate_recovery_codes(settings.TOTP_RECOVERY_CODE_COUNT)
        await self._replace_recovery_codes(user.id, new_codes)
        await self._reset_totp_rate_limit(user.id)

        logger.info("totp_recovery_codes_regenerated", user_id=str(user.id))
        return new_codes

    # Private helpers

    async def _consume_challenge(self, challenge_token: str) -> User:
        """Consume a challenge token from Redis and return the user.

        Uses GETDEL for atomic consume-and-delete.

        Raises:
            InvalidTOTPError: If challenge is expired or invalid.
        """
        redis = await get_redis()
        user_id_str = await redis.getdel(f"2fa:challenge:{challenge_token}")

        if not user_id_str:
            raise InvalidTOTPError("Challenge token expired or invalid.")

        # Bootstrapping identity from a challenge token: /auth/2fa/verify is
        # unauthenticated, so no tenant context exists yet, that's what this
        # lookup establishes. Without system_context(), User being
        # tenant-scoped means this raises TenantContextRequiredError on every
        # 2FA login, and the challenge has already been consumed by the GETDEL
        # above so the attempt cannot be retried.
        with system_context():
            user = await self.user_repository.get(UUID(user_id_str))
        if not user or not user.is_active:
            raise InvalidTOTPError("User not found or inactive.")

        return user

    async def _check_totp_rate_limit(self, user_id: UUID) -> None:
        """Rate-limit TOTP verification attempts.

        Allows at most 5 attempts per 5-minute window per user.

        Raises:
            TwoFactorRateLimitError: If the limit is exceeded.
        """
        redis = await get_redis()
        key = f"totp_attempts:{user_id}"
        count = await redis.incr(key)
        # Always refresh TTL to avoid race condition between INCR and EXPIRE.
        # Calling EXPIRE unconditionally is idempotent and safe.
        await redis.expire(key, settings.TOTP_RATE_LIMIT_WINDOW)
        if count > settings.TOTP_MAX_ATTEMPTS:
            logger.warning("totp_rate_limit_exceeded", user_id=str(user_id))
            raise TwoFactorRateLimitError(
                "Too many TOTP attempts. Try again in a few minutes."
            )

    async def _reset_totp_rate_limit(self, user_id: UUID) -> None:
        """Clear the TOTP attempt counter after a successful verification.

        Without this, a legitimate user who calls any rate-limited
        operation (TOTP or recovery-code verification, enrollment,
        disabling, regenerating codes) enough times within the window gets
        locked out even though every attempt succeeded, and each further
        successful call keeps refreshing the window's TTL, pushing the
        lockout out indefinitely instead of letting it expire.
        """
        redis = await get_redis()
        await redis.delete(f"totp_attempts:{user_id}")

    async def _prevent_replay(self, user_id: UUID, totp_code: str) -> None:
        """Prevent TOTP code replay within the same time window.

        Stores a used-code marker in Redis for 90 seconds (3 windows)
        using an atomic SET NX EX command. If the marker already exists,
        the code was already used.

        Raises:
            InvalidTOTPError: If the code was already used recently.
        """
        redis = await get_redis()
        key = f"totp_used:{user_id}:{totp_code}"
        # Single atomic SET NX EX, no race condition between setnx and expire.
        result = await redis.set(key, "1", nx=True, ex=settings.TOTP_REPLAY_TTL)
        if result is None:
            raise InvalidTOTPError("TOTP code has already been used.")

    async def _replace_recovery_codes(
        self,
        user_id: UUID,
        plaintext_codes: list[str],
    ) -> None:
        """Delete all existing recovery codes and insert new hashed ones.

        Wrapped in a savepoint so the delete-and-insert is atomic.

        Args:
            user_id: User whose codes to replace.
            plaintext_codes: New plaintext codes to hash and store.
        """
        async with self.session.begin_nested():
            await self._delete_all_recovery_codes(user_id)
            # Hash all codes concurrently off the event loop instead of
            # sequentially, bcrypt is ~100-250ms per call, so hashing 10
            # codes one at a time would block the loop for 1-2.5s while
            # this nested transaction is open.
            code_hashes = await asyncio.gather(
                *(
                    asyncio.to_thread(generate_password_hash, code)
                    for code in plaintext_codes
                )
            )
            for code_hash in code_hashes:
                new_code = TotpRecoveryCode(user_id=user_id, code_hash=code_hash)
                self.session.add(new_code)
            await self.session.flush()

    async def _delete_all_recovery_codes(self, user_id: UUID) -> None:
        """Hard-delete all recovery codes for a user.

        Args:
            user_id: User whose codes to delete.
        """
        statement = delete(TotpRecoveryCode).where(TotpRecoveryCode.user_id == user_id)  # type: ignore[arg-type]
        await self.session.execute(statement)
        await self.session.flush()
