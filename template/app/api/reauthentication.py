"""HTTP side of step-up authentication.

Routes that hand a token holder a lasting hold on the account call
`require_reauthentication` before doing anything, see
`app.services.reauthentication`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import settings
from app.models.user import User
from app.schemas.auth import ReauthenticationRequest
from app.services.reauthentication import ReauthenticationRequiredError
from app.services.reauthentication import require_fresh_proof
from app.services.two_factor_service import TwoFactorError
from app.services.two_factor_service import TwoFactorRateLimitError

#: The `code` of the 401 body, for a client to branch on.
REAUTH_REQUIRED = "REAUTH_REQUIRED"


def reauthentication_proof(
    proof: ReauthenticationRequest | None = None,
) -> ReauthenticationRequest:
    """Read the proof from an optional JSON body.

    An account with no password and no second factor proves itself with its
    session alone, so a request may carry no body at all.
    """
    return proof or ReauthenticationRequest()


ProofDependency = Annotated[ReauthenticationRequest, Depends(reauthentication_proof)]


async def require_reauthentication(
    session: AsyncSession,
    user: User,
    password: str | None,
    totp_code: str | None,
    auth_time: int | None,
) -> None:
    """Refuse the request unless the owner just proved themselves.

    Raises:
        HTTPException: 401 with `code` `REAUTH_REQUIRED` when the proof is
            missing, wrong or too old, 429 when too many were tried, 400
            when the account's second factor is unreadable.
    """
    try:
        await require_fresh_proof(session, user, password, totp_code, auth_time)
    except ReauthenticationRequiredError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": REAUTH_REQUIRED, "message": str(error)},
        ) from error
    except TwoFactorRateLimitError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(error),
            headers={"Retry-After": str(settings.TOTP_RATE_LIMIT_WINDOW)},
        ) from error
    except TwoFactorError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
