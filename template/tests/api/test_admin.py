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


@pytest.fixture
def second_factor(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stand in for the challenge store, which lives in Redis.

    `verify` holds what the next check answers, an account or an error,
    and `used` records which kind of code was checked.
    """
    from app.services.two_factor_service import TwoFactorAuthService

    state: dict[str, Any] = {"verify": None, "used": None}

    async def _issue(self, user: User) -> str:
        return "challenge"

    async def _answer(kind: str, token: str) -> User:
        state["used"] = kind
        assert token == "challenge"
        if isinstance(state["verify"], Exception):
            raise state["verify"]
        return state["verify"]

    async def _verify(self, token: str, code: str) -> User:
        return await _answer("authenticator", token)

    async def _recover(self, token: str, code: str) -> User:
        return await _answer("recovery", token)

    monkeypatch.setattr(TwoFactorAuthService, "issue_challenge", _issue)
    monkeypatch.setattr(TwoFactorAuthService, "verify_challenge", _verify)
    monkeypatch.setattr(
        TwoFactorAuthService, "verify_challenge_with_recovery", _recover
    )
    return state


async def _enrolled(session, email: str, slug: str) -> User:
    """A superuser who turned the second factor on."""
    user = await _account(session, email, slug)
    user.is_2fa_enabled = True
    session.add(user)
    await session.commit()
    return user


@pytest.mark.usefixtures("override_get_async_session")
class TestSecondFactor:
    """Tests for POST /admin/login and /admin/login/code with 2FA on."""

    @pytest.mark.anyio
    async def test_the_password_alone_opens_no_session(
        self, client: AsyncClient, session, second_factor: dict[str, Any]
    ) -> None:
        """The account asked for a second factor, so the form asks for it."""
        await _enrolled(session, "admin-2fa-ask@example.com", "admin-2fa-ask")

        response = await client.post(
            "/admin/login",
            data={"email": "admin-2fa-ask@example.com", "password": PASSWORD},
        )

        assert response.status_code == 200
        assert 'name="code"' in response.text
        assert client.cookies.get(SESSION_COOKIE) is None

    @pytest.mark.anyio
    async def test_the_right_code_opens_a_session(
        self, client: AsyncClient, session, second_factor: dict[str, Any]
    ) -> None:
        """Six digits are checked as an authenticator code."""
        user = await _enrolled(session, "admin-2fa-in@example.com", "admin-2fa-in")
        second_factor["verify"] = user

        response = await client.post(
            "/admin/login/code",
            data={"challenge_token": "challenge", "code": "123456"},
        )

        assert response.status_code == 303
        assert second_factor["used"] == "authenticator"
        assert client.cookies.get(SESSION_COOKIE)

    @pytest.mark.anyio
    async def test_a_recovery_code_opens_a_session(
        self, client: AsyncClient, session, second_factor: dict[str, Any]
    ) -> None:
        """Anything but six digits is checked as a recovery code."""
        user = await _enrolled(
            session, "admin-2fa-recover@example.com", "admin-2fa-recover"
        )
        second_factor["verify"] = user

        response = await client.post(
            "/admin/login/code",
            data={"challenge_token": "challenge", "code": "ABCD-EFGH"},
        )

        assert response.status_code == 303
        assert second_factor["used"] == "recovery"

    @pytest.mark.anyio
    async def test_a_wrong_code_opens_no_session(
        self, client: AsyncClient, second_factor: dict[str, Any]
    ) -> None:
        """Back to the password, with the usual sentence."""
        from app.services.two_factor_service import InvalidTOTPError

        second_factor["verify"] = InvalidTOTPError("Invalid TOTP code.")

        response = await client.post(
            "/admin/login/code",
            data={"challenge_token": "challenge", "code": "000000"},
        )

        assert response.status_code == 401
        assert "do not sign in here" in response.text
        assert client.cookies.get(SESSION_COOKIE) is None

    @pytest.mark.anyio
    async def test_too_many_codes_are_throttled(
        self, client: AsyncClient, second_factor: dict[str, Any]
    ) -> None:
        """Guessing six digits is the attack this step invites."""
        from app.services.two_factor_service import TwoFactorRateLimitError

        second_factor["verify"] = TwoFactorRateLimitError("Too many attempts.")

        response = await client.post(
            "/admin/login/code",
            data={"challenge_token": "challenge", "code": "000000"},
        )

        assert response.status_code == 429
        assert client.cookies.get(SESSION_COOKIE) is None

    @pytest.mark.anyio
    async def test_a_role_taken_away_meanwhile_opens_no_session(
        self, client: AsyncClient, session, second_factor: dict[str, Any]
    ) -> None:
        """The challenge outlives a demotion made between the two steps."""
        user = await _enrolled(
            session, "admin-2fa-demoted@example.com", "admin-2fa-demoted"
        )
        user.role = UserRole.USER
        second_factor["verify"] = user

        response = await client.post(
            "/admin/login/code",
            data={"challenge_token": "challenge", "code": "123456"},
        )

        assert response.status_code == 401
        assert client.cookies.get(SESSION_COOKIE) is None


class TestReachingTheSurface:
    """Tests for who the surface answers at all, by network."""

    @staticmethod
    async def _get(host: str, path: str) -> int:
        transport = ASGITransport(app=app, client=(host, 40000))
        async with AsyncClient(transport=transport, base_url="http://test") as one:
            return (await one.get(path)).status_code

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "path", ["/admin", "/admin/login", "/admin/static/admin.css"]
    )
    async def test_the_internet_finds_nothing(self, path: str) -> None:
        """The same 404 as a path that does not exist, stylesheet included."""
        assert await self._get("203.0.113.7", path) == 404

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "host", ["127.0.0.1", "100.101.102.103", "::ffff:10.0.0.4"]
    )
    async def test_a_private_network_finds_the_form(self, host: str) -> None:
        """The host, a `Tailscale` address, and IPv4 on a dual-stack socket."""
        assert await self._get(host, "/admin/login") == 200

    @pytest.mark.anyio
    @pytest.mark.usefixtures("override_get_async_session")
    async def test_the_api_still_answers_the_internet(self) -> None:
        """Only the surface is fenced off."""
        assert await self._get("203.0.113.7", "/api/v1/health") != 404

    def test_networks_are_read_comma_separated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The way an environment variable carries them."""
        from ipaddress import ip_network

        from app.core.settings import Settings

        monkeypatch.setenv("ADMIN_ALLOWED_NETWORKS", "10.1.0.0/16, 100.64.0.0/10")

        configured = Settings()

        assert configured.ADMIN_ALLOWED_NETWORKS == [
            ip_network("10.1.0.0/16"),
            ip_network("100.64.0.0/10"),
        ]


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
class TestTheSessionToken:
    """Tests for what the session cookie carries and where it works."""

    @pytest.mark.anyio
    async def test_the_api_refuses_it_as_a_bearer_token(
        self, client: AsyncClient, session
    ) -> None:
        """It lives two hours, far longer than an API token should."""
        await _account(session, "admin-bearer@example.com", "admin-bearer")
        await _sign_in(client, "admin-bearer@example.com")
        token = client.cookies.get(SESSION_COOKIE)

        response = await client.get(
            "/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 403

    @pytest.mark.anyio
    async def test_an_api_token_does_not_open_the_surface(
        self, client: AsyncClient, session
    ) -> None:
        """A token taken from the app is not an administration session."""
        from app.core.security import create_access_token

        user = await _account(session, "admin-api@example.com", "admin-api")
        client.cookies.set(
            SESSION_COOKIE,
            create_access_token(
                user.id, additional_claims={"token_version": user.token_version}
            ),
            path="/admin",
        )

        response = await client.get("/admin/users")

        assert response.status_code == 303
        assert response.headers["location"] == "/admin/login"

    @pytest.mark.anyio
    async def test_signing_out_everywhere_ends_it(
        self, client: AsyncClient, session
    ) -> None:
        """A changed password moves the version on, and the session ends."""
        user = await _account(session, "admin-version@example.com", "admin-version")
        await _sign_in(client, "admin-version@example.com")
        user.token_version += 1
        session.add(user)
        await session.commit()

        response = await client.get("/admin/users")

        assert response.status_code == 303
        assert response.headers["location"] == "/admin/login"

    @pytest.mark.anyio
    async def test_signing_out_revokes_it(
        self, client: AsyncClient, session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A copy of the cookie stops working, not just the deleted one."""
        from app.core.security import decode_token

        revoked: list[str] = []

        async def _revoke(identifier: str, expires_at: object) -> None:
            revoked.append(identifier)

        monkeypatch.setattr("app.admin.session.blacklist_token", _revoke)
        await _account(session, "admin-out@example.com", "admin-out")
        await _sign_in(client, "admin-out@example.com")
        issued = decode_token(client.cookies.get(SESSION_COOKIE))["jti"]

        response = await client.post(
            "/admin/logout", data={"csrf_token": client.cookies.get(CSRF_COOKIE)}
        )

        assert response.status_code == 303
        assert revoked == [issued]


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
