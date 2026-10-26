"""Step-up authentication for actions a stolen session must not perform.

An access token proves a sign in happened, not that the owner is at the
keyboard now. Enrolling a second factor, registering a passkey or deleting
the account hands a token holder a lasting hold on the account, so those
ask for something fresh first.
"""

from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import is_password_usable
from app.models.user import User
from app.services.two_factor_service import InvalidProofPasswordError
from app.services.two_factor_service import InvalidTOTPError
from app.services.two_factor_service import TwoFactorAuthService

#: How recent a sign in must be to count as proof for an account that has
#: neither a second factor nor a password to ask for.
REAUTH_WINDOW_SECONDS = 600


class ReauthenticationRequiredError(Exception):
    """The proof offered is missing, wrong or too old."""


async def require_fresh_proof(
    session: AsyncSession,
    user: User,
    password: str | None,
    totp_code: str | None,
    auth_time: int | None,
) -> None:
    """Demand fresh proof that the owner is behind the session.

    The strongest factor the account has decides what counts: a TOTP code
    when 2FA is on, else the password, else a sign in less than
    `REAUTH_WINDOW_SECONDS` old, read from the token's `auth_time` claim.
    Refreshing a session keeps `auth_time` as it was, so it never extends
    the window. Accounts made before the claim existed carry none and sign
    in again.

    Args:
        session: Database session.
        user: The signed-in user.
        password: Password sent with the request, if any.
        totp_code: Authenticator code sent with the request, if any.
        auth_time: `auth_time` claim of the access token, if it has one.

    Raises:
        ReauthenticationRequiredError: If the proof is missing, wrong or old.
        TwoFactorRateLimitError: If too many proofs were tried recently.
        TwoFactorError: If the account's TOTP secret cannot be read.
    """
    two_factor = TwoFactorAuthService(session)

    if user.is_2fa_enabled:
        if not totp_code:
            raise ReauthenticationRequiredError("An authenticator code is required.")
        try:
            await two_factor.verify_proof_code(user, totp_code)
        except InvalidTOTPError as error:
            raise ReauthenticationRequiredError(str(error)) from error
    elif is_password_usable(user.hashed_password):
        if not password:
            raise ReauthenticationRequiredError("The account password is required.")
        try:
            await two_factor.verify_proof_password(user, password)
        except InvalidProofPasswordError as error:
            raise ReauthenticationRequiredError(str(error)) from error
    elif auth_time is None or time.time() - auth_time > REAUTH_WINDOW_SECONDS:
        raise ReauthenticationRequiredError("Sign in again to continue.")
