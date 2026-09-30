"""Tests for the server-rendered administration surface.

Every case here is about who is allowed in and what a form is allowed to
do, because that is the whole risk of putting an administration tool on
the same origin as the API.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from typing import Literal

import pytest
from httpx import ASGITransport
from httpx import AsyncClient

from app.admin.session import CSRF_COOKIE
from app.admin.session import SESSION_COOKIE
from app.core.security import generate_password_hash
from app.main import app
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user import UserRole

PASSWORD = "Administrat0r!pass"


@pytest.fixture
def anyio_backend() -> Literal["asyncio"]:
    return "asyncio"


@pytest.fixture
async def client() -> Generator[AsyncClient, Any]:
    """A client that keeps cookies, which is the whole point here."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as one:
        yield one


async def _account(
    session,
    email: str,
    slug: str,
    role: UserRole = UserRole.SUPERUSER,
    is_active: bool = True,
) -> User:
    """Create a tenant and one account in it."""
    tenant = Tenant(name="Test", slug=slug)
    session.add(tenant)
    await session.flush()
    user = User(
        email=email,
        hashed_password=generate_password_hash(PASSWORD),
        full_name="Someone",
        tenant_id=tenant.id,
        role=role,
        is_active=is_active,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _sign_in(client: AsyncClient, email: str) -> None:
    """Sign in and keep the cookies on the client."""
    response = await client.post(
        "/admin/login",
        data={"email": email, "password": PASSWORD},
    )
    assert response.status_code == 303, response.text


@pytest.mark.usefixtures("override_get_async_session")
class TestSigningIn:
    """Tests for POST /admin/login."""

    @pytest.mark.anyio
    async def test_a_superuser_gets_a_session(
        self, client: AsyncClient, session
    ) -> None:
        """The two cookies the surface runs on are set."""
        await _account(session, "admin-in@example.com", "admin-in")

        await _sign_in(client, "admin-in@example.com")

        assert client.cookies.get(SESSION_COOKIE)
        assert client.cookies.get(CSRF_COOKIE)

    @pytest.mark.anyio
    async def test_an_ordinary_user_is_refused(
        self, client: AsyncClient, session
    ) -> None:
        """Right password, wrong privileges, and no session."""
        await _account(
            session,
            "admin-plain@example.com",
            "admin-plain",
            role=UserRole.USER,
        )

        response = await client.post(
            "/admin/login",
            data={"email": "admin-plain@example.com", "password": PASSWORD},
        )

        assert response.status_code == 401
        assert client.cookies.get(SESSION_COOKIE) is None

    @pytest.mark.anyio
    async def test_it_says_the_same_thing_however_it_failed(
        self, client: AsyncClient, session
    ) -> None:
        """A wrong password and a missing account read identically.

        Anything that distinguishes them turns this form into a way to
        find out who has an account and who is privileged.
        """
        await _account(session, "admin-same@example.com", "admin-same")

        wrong = await client.post(
            "/admin/login",
            data={"email": "admin-same@example.com", "password": "not-the-password"},
        )
        missing = await client.post(
            "/admin/login",
            data={"email": "nobody@example.com", "password": PASSWORD},
        )

        assert wrong.status_code == missing.status_code == 401
        assert "do not sign in here" in wrong.text
        assert "do not sign in here" in missing.text


@pytest.mark.usefixtures("override_get_async_session")
class TestReachingThePages:
    """Tests for who the pages answer."""

    @pytest.mark.anyio
    async def test_no_session_lands_on_the_form(self, client: AsyncClient) -> None:
        """A page, not the API's JSON error."""
        response = await client.get("/admin/users")

        assert response.status_code == 303
        assert response.headers["location"] == "/admin/login"

    @pytest.mark.anyio
    async def test_a_deactivated_administrator_is_turned_away(
        self, client: AsyncClient, session
    ) -> None:
        """The session is checked against the account, not just decoded.

        A token issued two hours ago says nothing about whether the
        account has been deactivated since.
        """
        user = await _account(session, "admin-off@example.com", "admin-off")
        await _sign_in(client, "admin-off@example.com")
        user.is_active = False
        session.add(user)
        await session.commit()

        response = await client.get("/admin/users")

        assert response.status_code == 303
        assert response.headers["location"] == "/admin/login"

    @pytest.mark.anyio
    async def test_the_list_shows_accounts(self, client: AsyncClient, session) -> None:
        """The page renders and carries the accounts."""
        await _account(session, "admin-list@example.com", "admin-list")
        await _sign_in(client, "admin-list@example.com")

        response = await client.get("/admin/users")

        assert response.status_code == 200
        assert "Accounts" in response.text


@pytest.mark.usefixtures("override_get_async_session")
class TestChangingAnAccount:
    """Tests for POST /admin/users/{id}/activation."""

    @pytest.mark.anyio
    async def test_a_form_without_its_token_is_refused(
        self, client: AsyncClient, session
    ) -> None:
        """Holding the cookie is not enough to act with it."""
        await _account(session, "admin-csrf@example.com", "admin-csrf")
        subject = await _account(
            session,
            "admin-csrf-subject@example.com",
            "admin-csrf-subject",
            role=UserRole.USER,
        )
        await _sign_in(client, "admin-csrf@example.com")

        response = await client.post(
            f"/admin/users/{subject.id}/activation",
            data={"is_active": "false"},
        )

        assert response.status_code == 403
        await session.refresh(subject)
        assert subject.is_active is True

    @pytest.mark.anyio
    async def test_an_account_is_deactivated(
        self, client: AsyncClient, session
    ) -> None:
        """With the token, the account stops being able to sign in."""
        await _account(session, "admin-act@example.com", "admin-act")
        subject = await _account(
            session,
            "admin-act-subject@example.com",
            "admin-act-subject",
            role=UserRole.USER,
        )
        await _sign_in(client, "admin-act@example.com")
        csrf = client.cookies.get(CSRF_COOKIE)

        response = await client.post(
            f"/admin/users/{subject.id}/activation",
            data={"is_active": "false", "csrf_token": csrf},
        )

        assert response.status_code == 303
        await session.refresh(subject)
        assert subject.is_active is False

    @pytest.mark.anyio
    async def test_an_administrator_cannot_deactivate_themselves(
        self, client: AsyncClient, session
    ) -> None:
        """Locking yourself out needs database access to undo."""
        user = await _account(session, "admin-self@example.com", "admin-self")
        await _sign_in(client, "admin-self@example.com")
        csrf = client.cookies.get(CSRF_COOKIE)

        response = await client.post(
            f"/admin/users/{user.id}/activation",
            data={"is_active": "false", "csrf_token": csrf},
        )

        assert response.status_code == 303
        await session.refresh(user)
        assert user.is_active is True
