"""The administration surface's pages.

One module while there are few pages. It grows a module per subject when
a subject has more than a list and a form.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Form
from fastapi import Query
from fastapi import Request
from fastapi import status
from fastapi.responses import HTMLResponse
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.admin.session import CsrfToken
from app.admin.session import CurrentAdministrator
from app.admin.session import close_session
from app.admin.session import open_session
from app.admin.session import verify_csrf
from app.core.logging import get_logger
from app.core.rate_limit import check_auth_rate_limit
from app.core.tenant import system_context
from app.dependencies import SessionDependency
from app.models.user import User
from app.models.user import UserRole
from app.repositories.user_repository import UserRepository
from app.services.auth_service import AuthenticationError
from app.services.auth_service import AuthService
from app.services.two_factor_service import TwoFactorAuthService
from app.services.two_factor_service import TwoFactorError
from app.services.two_factor_service import TwoFactorRateLimitError

logger = get_logger("admin")

router = APIRouter(prefix="/admin", include_in_schema=False)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

#: The one answer to every failed sign-in, see `sign_in`.
REFUSAL = "Those details do not sign in here."

#: How many accounts one page shows. Enough to scan, few enough that the
#: page renders in one go on a slow connection.
PAGE_SIZE = 50


@router.get("/login", response_class=HTMLResponse)
async def sign_in_form(request: Request) -> HTMLResponse:
    """The sign-in page."""
    return templates.TemplateResponse(request, "login.html", {"failure": None})


def _refused(request: Request, status_code: int) -> HTMLResponse:
    """Start over at the sign-in page, saying no more than `REFUSAL`."""
    return templates.TemplateResponse(
        request,
        "login.html",
        {"failure": REFUSAL},
        status_code=status_code,
    )


def _signed_in(user: User) -> RedirectResponse:
    """Open a session for `user` and send them to the first page."""
    logger.info("admin_signed_in", user_id=str(user.id))
    response = RedirectResponse(
        router.url_path_for("list_users"),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    open_session(response, user)
    return response


# `response_model=None`: FastAPI cannot build one from this union.
@router.post(
    "/login",
    response_model=None,
    dependencies=[Depends(check_auth_rate_limit)],
)
async def sign_in(
    request: Request,
    session: SessionDependency,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> HTMLResponse | RedirectResponse:
    """Check the credentials, then ask for the second factor if there is one.

    Every failure answers with the same sentence, or the form becomes a
    way to find out who has an account and who is privileged.

    No forgery token: there is no session to forge yet.
    """
    try:
        user = await AuthService(session).authenticate(email, password)
        if user.role != UserRole.SUPERUSER:
            raise PermissionError
    except (AuthenticationError, PermissionError):
        logger.warning("admin_sign_in_refused")
        return _refused(request, status.HTTP_401_UNAUTHORIZED)
    # The API asks for it on every login path, so this one must too, or
    # the most privileged session is the one a password alone opens.
    if user.is_2fa_enabled:
        challenge = await TwoFactorAuthService(session).issue_challenge(user)
        return templates.TemplateResponse(
            request,
            "second_factor.html",
            {"challenge_token": challenge},
        )
    return _signed_in(user)


@router.post(
    "/login/code",
    response_model=None,
    dependencies=[Depends(check_auth_rate_limit)],
)
async def confirm_second_factor(
    request: Request,
    session: SessionDependency,
    challenge_token: Annotated[str, Form(max_length=128)],
    code: Annotated[str, Form(max_length=32)],
) -> HTMLResponse | RedirectResponse:
    """Finish signing in with an authenticator or a recovery code.

    Six digits are an authenticator code, anything else a recovery code.
    The challenge is spent either way, so a wrong code starts over at
    the password.
    """
    service = TwoFactorAuthService(session)
    typed = code.strip()
    try:
        if typed.isdigit() and len(typed) == 6:
            user = await service.verify_challenge(challenge_token, typed)
        else:
            user = await service.verify_challenge_with_recovery(challenge_token, typed)
    except TwoFactorRateLimitError:
        logger.warning("admin_second_factor_throttled")
        return _refused(request, status.HTTP_429_TOO_MANY_REQUESTS)
    except TwoFactorError:
        logger.warning("admin_second_factor_refused")
        return _refused(request, status.HTTP_401_UNAUTHORIZED)
    # Checked again: the challenge outlives a role change made meanwhile.
    if not user.is_active or user.role != UserRole.SUPERUSER:
        return _refused(request, status.HTTP_401_UNAUTHORIZED)
    return _signed_in(user)


@router.post("/logout", dependencies=[Depends(verify_csrf)])
async def sign_out(administrator: CurrentAdministrator) -> RedirectResponse:
    """End the session."""
    logger.info("admin_signed_out", user_id=str(administrator.id))
    response = RedirectResponse(
        router.url_path_for("sign_in_form"),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    close_session(response)
    return response


@router.get("", response_class=RedirectResponse)
async def home() -> RedirectResponse:
    """Send the bare path somewhere useful."""
    return RedirectResponse(
        router.url_path_for("list_users"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/users", response_class=HTMLResponse)
async def list_users(
    request: Request,
    session: SessionDependency,
    administrator: CurrentAdministrator,
    csrf: CsrfToken,
    query: Annotated[str, Query(alias="q", max_length=100)] = "",
    page: Annotated[int, Query(ge=1)] = 1,
) -> HTMLResponse:
    """Accounts, newest first, or the ones matching a search.

    By name only: the email column is encrypted at rest with a randomised
    ciphertext, so a partial match has nothing to match against.
    """
    repository = UserRepository(session)
    skip = (page - 1) * PAGE_SIZE
    typed = query.strip()
    # Across every tenant: a superuser here is the operator of the
    # deployment, not a member of one tenant, and the account causing a
    # problem is usually in a tenant the operator does not belong to.
    with system_context():
        if typed:
            users, total = await repository.search(typed, skip=skip, limit=PAGE_SIZE)
        else:
            found, total = await repository.list(
                skip=skip,
                limit=PAGE_SIZE,
                sort_by="created_at",
                sort_order="desc",
            )
            users = list(found)
    return templates.TemplateResponse(
        request,
        "users.html",
        {
            "administrator": administrator,
            "csrf_token": csrf,
            "users": users,
            "query": typed,
            "page": page,
            "has_more": skip + len(users) < total,
            "total": total,
        },
    )


@router.post("/users/{user_id}/activation", dependencies=[Depends(verify_csrf)])
async def set_user_activation(
    session: SessionDependency,
    administrator: CurrentAdministrator,
    user_id: UUID,
    is_active: Annotated[bool, Form()],
) -> RedirectResponse:
    """Let an account in, or stop letting it in.

    Deactivating rather than deleting, so the content, the audit trail
    and the decision itself can all be taken back. Deletion is the
    account holder's to ask for and goes through the API.

    Refuses the signed-in administrator: locking yourself out of the tool
    that unlocks accounts needs database access to undo.
    """
    if user_id == administrator.id:
        return RedirectResponse(
            router.url_path_for("list_users"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    # Cross-tenant, see `list_users`.
    with system_context():
        await UserRepository(session).update(user_id, {"is_active": is_active})
    await session.commit()
    logger.info(
        "admin_set_user_activation",
        actor_id=str(administrator.id),
        user_id=str(user_id),
        is_active=is_active,
    )
    return RedirectResponse(
        router.url_path_for("list_users"),
        status_code=status.HTTP_303_SEE_OTHER,
    )
