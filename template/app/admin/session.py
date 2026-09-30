"""Signing in to the administration surface, and staying signed in.

A cookie rather than a bearer token, because a browser loading a page
sends cookies and nothing else. It carries the same signed token the API
issues, so one place still decides who somebody is.

Travelling in a cookie is what makes cross-site request forgery
possible, so every form here carries a token checked against a second
cookie.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Cookie
from fastapi import Depends
from fastapi import Form
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi import status

from app.core.security import create_access_token
from app.core.security import decode_token
from app.core.settings import settings
from app.core.tenant import system_context
from app.dependencies import SessionDependency
from app.models.user import User
from app.models.user import UserRole
from app.repositories.user_repository import UserRepository

#: The signed-in administrator.
SESSION_COOKIE = "admin_session"

#: The value every form must send back, see `verify_csrf`.
CSRF_COOKIE = "admin_csrf"

#: Short on purpose. An administration session is the most valuable one
#: in the system and is used in bursts, so an unattended tab stops being
#: a way in well before the end of the day.
SESSION_LIFETIME = timedelta(hours=2)


def _is_secure() -> bool:
    """Whether cookies may only travel over TLS.

    Off locally, where there is no certificate and a secure cookie would
    never be sent at all.
    """
    return settings.ENVIRONMENT != "local"


def open_session(response: Response, user: User) -> None:
    """Sign `user` in and hand the browser its forgery token."""
    response.set_cookie(
        SESSION_COOKIE,
        create_access_token(user.id, expires_delta=SESSION_LIFETIME),
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        secure=_is_secure(),
        # Strict, not Lax: nothing links into this surface, so there is
        # no navigation it breaks, and Lax still sends on a top-level GET.
        samesite="strict",
        path="/admin",
    )
    response.set_cookie(
        CSRF_COOKIE,
        secrets.token_urlsafe(32),
        max_age=int(SESSION_LIFETIME.total_seconds()),
        # HttpOnly too: the template writes the value into the form, so
        # nothing in the page needs to read it.
        httponly=True,
        secure=_is_secure(),
        samesite="strict",
        path="/admin",
    )


def close_session(response: Response) -> None:
    """Sign out, by throwing both cookies away."""
    response.delete_cookie(SESSION_COOKIE, path="/admin")
    response.delete_cookie(CSRF_COOKIE, path="/admin")


class NotSignedInError(Exception):
    """Raised when there is nobody behind the request.

    Caught by the surface's own handler, which answers with the sign-in
    page rather than the API's JSON error.
    """


async def current_administrator(
    session: SessionDependency,
    admin_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> User:
    """The superuser this request belongs to.

    Checked against the account rather than trusted from the token: a
    session issued two hours ago says nothing about whether the account
    has since been deactivated or had its role taken away.

    Raises:
        NotSignedInError: If there is no usable session.
    """
    if not admin_session:
        raise NotSignedInError
    try:
        claims = decode_token(admin_session)
    except jwt.InvalidTokenError as error:
        raise NotSignedInError from error
    subject = claims.get("sub")
    if not subject:
        raise NotSignedInError
    try:
        user_id = UUID(str(subject))
    except ValueError as error:
        raise NotSignedInError from error
    # Cross-tenant, see `router.list_users`.
    with system_context():
        user = await UserRepository(session).get(user_id)
    if user is None or not user.is_active or user.role != UserRole.SUPERUSER:
        raise NotSignedInError
    return user


CurrentAdministrator = Annotated[User, Depends(current_administrator)]


def csrf_token(request: Request) -> str:
    """The token this request's forms must carry back."""
    return request.cookies.get(CSRF_COOKIE, "")


CsrfToken = Annotated[str, Depends(csrf_token)]


async def verify_csrf(
    request: Request,
    csrf: Annotated[str, Form(alias="csrf_token")] = "",
) -> None:
    """Refuse a form that did not come from a page this surface drew.

    Double submit: the value is in a cookie the page cannot read and in a
    field the server wrote, so anything able to send both already had the
    page. Compared in constant time.

    Raises:
        HTTPException: 403 when the two do not match.
    """
    expected = request.cookies.get(CSRF_COOKIE, "")
    if not expected or not secrets.compare_digest(expected, csrf):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This form has expired. Reload the page and try again.",
        )
