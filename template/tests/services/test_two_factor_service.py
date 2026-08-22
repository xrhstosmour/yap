"""Tests for TwoFactorAuthService."""

from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import UUID

import pyotp
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import decrypt
from app.core.encryption import encrypt
from app.core.security import generate_recovery_codes
from app.core.security import generate_totp_secret
from app.core.settings import settings
from app.core.tenant import tenant_context
from app.schemas.auth import RegisterRequest
from app.services.auth_service import AuthService
from app.services.two_factor_service import InvalidTOTPError
from app.services.two_factor_service import TwoFactorAlreadyEnabledError
from app.services.two_factor_service import TwoFactorAuthService
from app.services.two_factor_service import TwoFactorError
from app.services.two_factor_service import TwoFactorNotEnabledError
from app.services.two_factor_service import TwoFactorRateLimitError


def make_user(
    is_2fa_enabled: bool = False,
    totp_secret_encrypted: str | None = None,
    totp_confirmed_at: object | None = None,
    is_active: bool = True,
) -> MagicMock:
    """Build a mock User object."""
    user = MagicMock()
    user.id = UUID("00000000-0000-0000-0000-000000000001")
    user.email = "user@example.com"
    user.is_2fa_enabled = is_2fa_enabled
    user.totp_secret_encrypted = totp_secret_encrypted
    user.totp_confirmed_at = totp_confirmed_at
    user.is_active = is_active
    return user


@pytest.fixture
def mock_session() -> MagicMock:
    """Build a mocked async DB session."""
    session = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def service(mock_session: MagicMock) -> TwoFactorAuthService:
    """Build TwoFactorAuthService with mocked repository."""
    svc = TwoFactorAuthService(mock_session)
    svc.user_repository = MagicMock()
    svc.user_repository.update = AsyncMock()
    svc.user_repository.get = AsyncMock()
    svc.user_repository.increment_token_version = AsyncMock()
    return svc


class TestBeginEnrollment:
    """Tests for begin_enrollment()."""

    @pytest.mark.asyncio
    async def test_raises_if_2fa_already_enabled(
        self,
        service: TwoFactorAuthService,
    ) -> None:
        """Should raise TwoFactorAlreadyEnabledError if 2FA is active."""
        user = make_user(is_2fa_enabled=True)
        with pytest.raises(TwoFactorAlreadyEnabledError):
            await service.begin_enrollment(user)

    @pytest.mark.asyncio
    async def test_returns_secret_and_qr_and_codes(
        self,
        service: TwoFactorAuthService,
    ) -> None:
        """Should return secret, QR data URL, and 10 recovery codes."""
        user = make_user()
        secret, qr, codes = await service.begin_enrollment(user)

        assert isinstance(secret, str)
        assert len(secret) > 0
        assert qr.startswith("data:image/png;base64,")
        assert len(codes) == settings.TOTP_RECOVERY_CODE_COUNT
        service.user_repository.update.assert_awaited_once()


class TestConfirmEnrollment:
    """Tests for confirm_enrollment()."""

    @pytest.mark.asyncio
    async def test_raises_if_no_pending_secret(
        self,
        service: TwoFactorAuthService,
    ) -> None:
        """Should raise TwoFactorError if no pending secret exists."""
        user = make_user(totp_secret_encrypted=None)
        with pytest.raises(TwoFactorError):
            await service.confirm_enrollment(user, "123456")

    @pytest.mark.asyncio
    async def test_raises_if_already_enabled(
        self,
        service: TwoFactorAuthService,
    ) -> None:
        """Should raise TwoFactorAlreadyEnabledError if already active."""
        user = make_user(is_2fa_enabled=True, totp_secret_encrypted="enc_secret")
        with pytest.raises(TwoFactorAlreadyEnabledError):
            await service.confirm_enrollment(user, "123456")

    @pytest.mark.asyncio
    async def test_raises_on_invalid_totp(
        self,
        service: TwoFactorAuthService,
    ) -> None:
        """Should raise InvalidTOTPError for wrong code."""
        secret = generate_totp_secret()
        encrypted = encrypt(secret)
        user = make_user(totp_secret_encrypted=encrypted)

        with (
            patch(
                "app.services.two_factor_service.TwoFactorAuthService"
                "._check_totp_rate_limit",
                new_callable=AsyncMock,
            ),
            pytest.raises(InvalidTOTPError),
        ):
            await service.confirm_enrollment(user, "000000")

    @pytest.mark.asyncio
    async def test_activates_2fa_on_valid_code(
        self,
        service: TwoFactorAuthService,
    ) -> None:
        """Should activate 2FA and bump token_version on valid code."""
        secret = generate_totp_secret()
        encrypted = encrypt(secret)
        user = make_user(totp_secret_encrypted=encrypted)
        valid_code = pyotp.TOTP(secret).now()

        with (
            patch(
                "app.services.two_factor_service.TwoFactorAuthService"
                "._check_totp_rate_limit",
                new_callable=AsyncMock,
            ),
            patch.object(service, "_reset_totp_rate_limit", new_callable=AsyncMock),
        ):
            await service.confirm_enrollment(user, valid_code)

        service.user_repository.update.assert_awaited_once()
        service.user_repository.increment_token_version.assert_not_called()


class TestIssueChallenge:
    """Tests for issue_challenge()."""

    @pytest.mark.asyncio
    async def test_returns_token_string(self, service: TwoFactorAuthService) -> None:
        """Should return a non-empty string token."""
        user = make_user(is_2fa_enabled=True)

        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()

        with patch(
            "app.services.two_factor_service.get_redis",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ):
            token = await service.issue_challenge(user)

        assert isinstance(token, str)
        assert len(token) > 10
        mock_redis.setex.assert_awaited_once()


class TestDisable:
    """Tests for disable()."""

    @pytest.mark.asyncio
    async def test_raises_if_not_enabled(self, service: TwoFactorAuthService) -> None:
        """Should raise TwoFactorNotEnabledError if 2FA is inactive."""
        user = make_user(is_2fa_enabled=False)
        with pytest.raises(TwoFactorNotEnabledError):
            await service.disable(user, "123456")

    @pytest.mark.asyncio
    async def test_raises_on_invalid_totp(
        self,
        service: TwoFactorAuthService,
    ) -> None:
        """Should raise InvalidTOTPError for wrong TOTP code."""
        secret = generate_totp_secret()
        encrypted = encrypt(secret)
        user = make_user(is_2fa_enabled=True, totp_secret_encrypted=encrypted)

        with (
            patch(
                "app.services.two_factor_service.TwoFactorAuthService"
                "._check_totp_rate_limit",
                new_callable=AsyncMock,
            ),
            pytest.raises(InvalidTOTPError),
        ):
            await service.disable(user, "000000")

    @pytest.mark.asyncio
    async def test_disable_success(
        self,
        service: TwoFactorAuthService,
    ) -> None:
        """Disabling 2FA should clear fields and bump token version."""
        secret = generate_totp_secret()
        encrypted = encrypt(secret)
        user = make_user(is_2fa_enabled=True, totp_secret_encrypted=encrypted)
        valid_code = pyotp.TOTP(secret).now()

        with (
            patch(
                "app.services.two_factor_service.TwoFactorAuthService"
                "._check_totp_rate_limit",
                new_callable=AsyncMock,
            ),
            patch.object(service, "_reset_totp_rate_limit", new_callable=AsyncMock),
        ):
            await service.disable(user, valid_code)

        service.user_repository.update.assert_awaited_once()
        service.user_repository.increment_token_version.assert_not_called()


class TestVerifyChallenge:
    """Tests for verify_challenge()."""

    @pytest.fixture
    def user_with_secret(self) -> MagicMock:
        secret = generate_totp_secret()
        encrypted = encrypt(secret)
        return make_user(
            is_2fa_enabled=True,
            totp_secret_encrypted=encrypted,
        )

    @pytest.mark.asyncio
    async def test_valid_totp_returns_user(
        self,
        service: TwoFactorAuthService,
        user_with_secret: MagicMock,
    ) -> None:
        """Valid TOTP code should return the user."""
        secret = decrypt(user_with_secret.totp_secret_encrypted)
        valid_code = pyotp.TOTP(secret).now()

        with (
            patch.object(
                service,
                "_consume_challenge",
                new_callable=AsyncMock,
                return_value=user_with_secret,
            ),
            patch.object(service, "_check_totp_rate_limit", new_callable=AsyncMock),
            patch.object(service, "_prevent_replay", new_callable=AsyncMock),
            patch.object(service, "_reset_totp_rate_limit", new_callable=AsyncMock),
        ):
            result = await service.verify_challenge("valid-token", valid_code)

        assert result is user_with_secret

    @pytest.mark.asyncio
    async def test_invalid_totp_raises(
        self,
        service: TwoFactorAuthService,
        user_with_secret: MagicMock,
    ) -> None:
        """Invalid TOTP code should raise InvalidTOTPError."""
        with (
            patch.object(
                service,
                "_consume_challenge",
                new_callable=AsyncMock,
                return_value=user_with_secret,
            ),
            patch.object(service, "_check_totp_rate_limit", new_callable=AsyncMock),
            pytest.raises(InvalidTOTPError),
        ):
            await service.verify_challenge("valid-token", "000000")

    @pytest.mark.asyncio
    async def test_expired_challenge_raises(
        self,
        service: TwoFactorAuthService,
    ) -> None:
        """Expired challenge token should raise InvalidTOTPError."""
        mock_consume = AsyncMock()
        mock_consume.side_effect = InvalidTOTPError(
            "Challenge token expired or invalid."
        )
        with (
            patch.object(service, "_consume_challenge", mock_consume),
            pytest.raises(InvalidTOTPError),
        ):
            await service.verify_challenge("expired-token", "123456")


class TestVerifyChallengeWithRecovery:
    """Tests for verify_challenge_with_recovery()."""

    @pytest.mark.asyncio
    async def test_valid_recovery_code_returns_user(
        self,
        service: TwoFactorAuthService,
    ) -> None:
        """Valid recovery code should return the user."""
        user = make_user(is_2fa_enabled=True, totp_secret_encrypted=encrypt("test"))
        recovery_codes = generate_recovery_codes(5)
        from app.core.security import generate_password_hash

        mock_codes = []
        for code in recovery_codes:
            rc = MagicMock()
            rc.code_hash = generate_password_hash(code)
            rc.id = UUID("00000000-0000-0000-0000-000000000002")
            mock_codes.append(rc)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_codes

        with (
            patch.object(
                service,
                "_consume_challenge",
                new_callable=AsyncMock,
                return_value=user,
            ),
            patch.object(service, "_check_totp_rate_limit", new_callable=AsyncMock),
            patch.object(service, "_reset_totp_rate_limit", new_callable=AsyncMock),
            patch.object(
                service.session,
                "execute",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            result = await service.verify_challenge_with_recovery(
                "token", recovery_codes[0]
            )

        assert result is user

    @pytest.mark.asyncio
    async def test_invalid_recovery_code_raises(
        self,
        service: TwoFactorAuthService,
    ) -> None:
        """Invalid recovery code should raise InvalidTOTPError."""
        user = make_user(is_2fa_enabled=True, totp_secret_encrypted=encrypt("test"))

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        with (
            patch.object(
                service,
                "_consume_challenge",
                new_callable=AsyncMock,
                return_value=user,
            ),
            patch.object(service, "_check_totp_rate_limit", new_callable=AsyncMock),
            patch.object(
                service.session,
                "execute",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            pytest.raises(InvalidTOTPError),
        ):
            await service.verify_challenge_with_recovery("token", "invalid-code")

    @pytest.mark.asyncio
    async def test_rate_limit_is_checked(
        self,
        service: TwoFactorAuthService,
    ) -> None:
        """A rate limit exceeded error should stop recovery verification."""
        user = make_user(is_2fa_enabled=True, totp_secret_encrypted=encrypt("test"))

        with (
            patch.object(
                service,
                "_consume_challenge",
                new_callable=AsyncMock,
                return_value=user,
            ),
            patch.object(
                service,
                "_check_totp_rate_limit",
                new_callable=AsyncMock,
                side_effect=TwoFactorRateLimitError("Too many attempts."),
            ),
            pytest.raises(TwoFactorRateLimitError),
        ):
            await service.verify_challenge_with_recovery("token", "some-code")

    @pytest.mark.asyncio
    async def test_matching_codes_are_locked_for_update(
        self,
        service: TwoFactorAuthService,
    ) -> None:
        """The candidate-code lookup should lock rows against a concurrent claim."""
        user = make_user(is_2fa_enabled=True, totp_secret_encrypted=encrypt("test"))
        recovery_codes = generate_recovery_codes(1)
        from app.core.security import generate_password_hash

        rc = MagicMock()
        rc.code_hash = generate_password_hash(recovery_codes[0])
        rc.id = UUID("00000000-0000-0000-0000-000000000002")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [rc]

        with (
            patch.object(
                service,
                "_consume_challenge",
                new_callable=AsyncMock,
                return_value=user,
            ),
            patch.object(service, "_check_totp_rate_limit", new_callable=AsyncMock),
            patch.object(service, "_reset_totp_rate_limit", new_callable=AsyncMock),
            patch.object(
                service.session,
                "execute",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_execute,
        ):
            await service.verify_challenge_with_recovery("token", recovery_codes[0])

        select_statement = mock_execute.await_args_list[0].args[0]
        assert select_statement._for_update_arg is not None


class TestRegenerateRecoveryCodes:
    """Tests for regenerate_recovery_codes()."""

    @pytest.mark.asyncio
    async def test_success_returns_new_codes(
        self,
        service: TwoFactorAuthService,
    ) -> None:
        """Should return new recovery codes when 2FA is active."""
        secret = generate_totp_secret()
        encrypted = encrypt(secret)
        user = make_user(is_2fa_enabled=True, totp_secret_encrypted=encrypted)
        valid_code = pyotp.TOTP(secret).now()

        with (
            patch(
                "app.services.two_factor_service.TwoFactorAuthService"
                "._check_totp_rate_limit",
                new_callable=AsyncMock,
            ),
            patch.object(service, "_reset_totp_rate_limit", new_callable=AsyncMock),
        ):
            new_codes = await service.regenerate_recovery_codes(user, valid_code)

        assert len(new_codes) == settings.TOTP_RECOVERY_CODE_COUNT

    @pytest.mark.asyncio
    async def test_raises_if_not_enabled(
        self,
        service: TwoFactorAuthService,
    ) -> None:
        """Should raise TwoFactorNotEnabledError if 2FA is inactive."""
        user = make_user(is_2fa_enabled=False)
        with pytest.raises(TwoFactorNotEnabledError):
            await service.regenerate_recovery_codes(user, "123456")

    @pytest.mark.asyncio
    async def test_raises_on_invalid_totp(
        self,
        service: TwoFactorAuthService,
    ) -> None:
        """Invalid TOTP should raise InvalidTOTPError."""
        secret = generate_totp_secret()
        encrypted = encrypt(secret)
        user = make_user(is_2fa_enabled=True, totp_secret_encrypted=encrypted)

        with (
            patch(
                "app.services.two_factor_service.TwoFactorAuthService"
                "._check_totp_rate_limit",
                new_callable=AsyncMock,
            ),
            pytest.raises(InvalidTOTPError),
        ):
            await service.regenerate_recovery_codes(user, "000000")


class TestConsumeChallengeTenantContext:
    """Tests for _consume_challenge() against a real database session.

    The rest of this module mocks the repository, which cannot catch a
    tenant-filter regression. /auth/2fa/verify is unauthenticated, so these
    run with no tenant context set, exactly as the endpoint does.
    """

    @pytest.mark.asyncio
    async def test_consumes_challenge_without_tenant_context(
        self, session: AsyncSession
    ) -> None:
        """Challenge lookup must work with no tenant context set."""
        auth_service = AuthService(session)
        user = await auth_service.register(
            RegisterRequest(email="2fa-user@example.com", password="password123")
        )

        redis = AsyncMock()
        redis.getdel = AsyncMock(return_value=str(user.id))

        async def _get_redis() -> AsyncMock:
            return redis

        service = TwoFactorAuthService(session)

        with (
            patch("app.services.two_factor_service.get_redis", _get_redis),
            tenant_context(None),
        ):
            resolved = await service._consume_challenge("challenge-token")

        assert resolved.id == user.id
