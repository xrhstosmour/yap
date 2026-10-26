"""Unit tests for step-up authentication."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pyotp
import pytest

from app.core.encryption import encrypt
from app.core.security import generate_password_hash
from app.core.security import generate_totp_secret
from app.core.security import generate_unusable_password_hash
from app.services.reauthentication import REAUTH_WINDOW_SECONDS
from app.services.reauthentication import ReauthenticationRequiredError
from app.services.reauthentication import require_fresh_proof
from app.services.two_factor_service import ProofPasswordRateLimitError
from app.services.two_factor_service import TwoFactorAuthService
from app.services.two_factor_service import TwoFactorRateLimitError


def _user(**overrides: object) -> MagicMock:
    """Build a user with a password and no second factor."""
    user = MagicMock()
    user.id = "user-1"
    user.is_2fa_enabled = False
    user.totp_secret_encrypted = None
    user.hashed_password = generate_password_hash("correct-password")
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


@pytest.fixture(autouse=True)
def _attempt_counter_and_replay_guard():
    """Replace the Redis attempt counter and replay marker."""
    with (
        patch.object(TwoFactorAuthService, "_check_totp_rate_limit", AsyncMock()),
        patch.object(TwoFactorAuthService, "_reset_totp_rate_limit", AsyncMock()),
        patch.object(TwoFactorAuthService, "_prevent_replay", AsyncMock()),
        patch.object(TwoFactorAuthService, "_check_password_proof_limit", AsyncMock()),
        patch.object(TwoFactorAuthService, "_reset_password_proof_limit", AsyncMock()),
    ):
        yield


class TestPasswordAccount:
    """An account with a usable password proves itself with it."""

    @pytest.mark.asyncio
    async def test_correct_password_passes(self) -> None:
        """The right password is accepted."""
        await require_fresh_proof(MagicMock(), _user(), "correct-password", None, None)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("password", [None, "", "wrong-password"])
    async def test_missing_or_wrong_password_is_refused(
        self, password: str | None
    ) -> None:
        """No proof, or a wrong one, is refused."""
        with pytest.raises(ReauthenticationRequiredError):
            await require_fresh_proof(MagicMock(), _user(), password, None, None)

    @pytest.mark.asyncio
    async def test_a_recent_sign_in_does_not_replace_the_password(self) -> None:
        """Only an account without a password may lean on `auth_time`."""
        with pytest.raises(ReauthenticationRequiredError):
            await require_fresh_proof(
                MagicMock(), _user(), None, None, int(time.time())
            )

    @pytest.mark.asyncio
    async def test_attempt_limit_surfaces_as_rate_limit(self) -> None:
        """Guessing passwords with a stolen token is throttled."""
        with (
            patch.object(
                TwoFactorAuthService,
                "_check_password_proof_limit",
                AsyncMock(side_effect=ProofPasswordRateLimitError("slow down")),
            ),
            pytest.raises(TwoFactorRateLimitError),
        ):
            await require_fresh_proof(MagicMock(), _user(), "guess", None, None)


class TestTwoFactorAccount:
    """An account with 2FA proves itself with a code, not the password."""

    @staticmethod
    def _enrolled() -> tuple[MagicMock, str]:
        secret = generate_totp_secret()
        return (
            _user(is_2fa_enabled=True, totp_secret_encrypted=encrypt(secret)),
            secret,
        )

    @pytest.mark.asyncio
    async def test_valid_code_passes(self) -> None:
        """A current code is accepted."""
        user, secret = self._enrolled()

        await require_fresh_proof(
            MagicMock(), user, None, pyotp.TOTP(secret).now(), None
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [None, "000000", "12"])
    async def test_missing_or_wrong_code_is_refused(self, code: str | None) -> None:
        """No code, a wrong one or a malformed one is refused."""
        user, _ = self._enrolled()

        with pytest.raises(ReauthenticationRequiredError):
            await require_fresh_proof(MagicMock(), user, None, code, None)

    @pytest.mark.asyncio
    async def test_password_alone_is_not_enough(self) -> None:
        """Knowing the password must not stand in for the second factor."""
        user, _ = self._enrolled()

        with pytest.raises(ReauthenticationRequiredError):
            await require_fresh_proof(
                MagicMock(), user, "correct-password", None, int(time.time())
            )


class TestAccountWithoutPassword:
    """An account made by a social sign in needs a recent sign in."""

    @pytest.mark.asyncio
    async def test_recent_sign_in_passes(self) -> None:
        """A sign in inside the window is enough."""
        user = _user(hashed_password=generate_unusable_password_hash())

        await require_fresh_proof(MagicMock(), user, None, None, int(time.time()) - 60)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "auth_time",
        [None, 0, int(time.time()) - REAUTH_WINDOW_SECONDS - 5],
        ids=["missing", "epoch", "past_window"],
    )
    async def test_old_or_missing_sign_in_is_refused(
        self, auth_time: int | None
    ) -> None:
        """An old session, or one from before the claim existed, signs in again."""
        user = _user(hashed_password=generate_unusable_password_hash())

        with pytest.raises(ReauthenticationRequiredError):
            await require_fresh_proof(MagicMock(), user, None, None, auth_time)
