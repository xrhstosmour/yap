"""Tests for registering a device to receive push notifications."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from typing import Literal

import pytest
from httpx import ASGITransport
from httpx import AsyncClient

from app.core.tenant import system_context
from app.main import app
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.device_token_repository import DeviceTokenRepository


@pytest.fixture
def anyio_backend() -> Literal["asyncio"]:
    return "asyncio"


@pytest.fixture
async def client() -> Generator[AsyncClient, Any]:
    """Async HTTP test client for the full app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _account(session, email: str, slug: str) -> tuple[User, str]:
    """Create a tenant, a user in it, and a bearer token for that user."""
    from app.services.auth_service import AuthService

    tenant = Tenant(name="Test", slug=slug)
    session.add(tenant)
    await session.flush()

    user = User(
        email=email,
        hashed_password="hash",
        tenant_id=tenant.id,
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    return user, AuthService(session).create_tokens(user).access_token


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.usefixtures("override_get_async_session")
class TestRegisterDevice:
    """Tests for POST /devices."""

    @pytest.mark.anyio
    async def test_registers_a_token_for_the_caller(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        """The install is claimed and reported back without its token."""
        _, token = await _account(session, "device-one@example.com", "device-one")

        response = await client.post(
            "/api/v1/devices",
            json={"token": "token-one", "platform": "ios"},
            headers=_headers(token),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["platform"] == "ios"
        # Never echoed back. The client has it, and returning it only puts
        # another copy of it in a log.
        assert "token" not in body

    @pytest.mark.anyio
    async def test_registering_twice_does_not_make_two_rows(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        """The client calls this on every launch, so it has to be idempotent."""
        _, token = await _account(session, "device-twice@example.com", "device-twice")

        first = await client.post(
            "/api/v1/devices",
            json={"token": "token-twice", "platform": "android"},
            headers=_headers(token),
        )
        second = await client.post(
            "/api/v1/devices",
            json={"token": "token-twice", "platform": "android"},
            headers=_headers(token),
        )

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json()["id"] == second.json()["id"]

    @pytest.mark.anyio
    async def test_a_handed_over_device_moves_to_the_new_account(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        """Otherwise the previous account keeps getting notifications on it."""
        first_user, first_token = await _account(
            session, "device-old@example.com", "device-old"
        )
        second_user, second_token = await _account(
            session, "device-new@example.com", "device-new"
        )

        await client.post(
            "/api/v1/devices",
            json={"token": "shared-phone", "platform": "ios"},
            headers=_headers(first_token),
        )
        await client.post(
            "/api/v1/devices",
            json={"token": "shared-phone", "platform": "ios"},
            headers=_headers(second_token),
        )

        devices = DeviceTokenRepository(session)
        with system_context():
            assert await devices.for_users([first_user.id]) == []
            moved = await devices.for_users([second_user.id])
        assert [row.token for row in moved] == ["shared-phone"]


@pytest.mark.usefixtures("override_get_async_session")
class TestForgetDevice:
    """Tests for DELETE /devices/{token}."""

    @pytest.mark.anyio
    async def test_sign_out_stops_delivery_to_that_install(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        """The token is gone from the send audience afterwards."""
        user, token = await _account(session, "device-bye@example.com", "device-bye")
        await client.post(
            "/api/v1/devices",
            json={"token": "token-bye", "platform": "ios"},
            headers=_headers(token),
        )

        response = await client.delete(
            "/api/v1/devices/token-bye",
            headers=_headers(token),
        )

        assert response.status_code == 204, response.text
        with system_context():
            assert await DeviceTokenRepository(session).for_users([user.id]) == []

    @pytest.mark.anyio
    async def test_forgetting_an_unknown_token_is_not_an_error(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        """A device that was never registered is already what was asked for."""
        _, token = await _account(
            session, "device-unknown@example.com", "device-unknown"
        )

        response = await client.delete(
            "/api/v1/devices/never-registered",
            headers=_headers(token),
        )

        assert response.status_code == 204, response.text

    @pytest.mark.anyio
    async def test_cannot_silence_somebody_else_s_phone(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        """A push token is not a secret, so holding one must not be enough.

        The answer is still 204, because the caller is not entitled to
        learn whether the token exists, but the row survives.
        """
        owner, owner_token = await _account(
            session, "device-owner@example.com", "device-owner"
        )
        _, attacker_token = await _account(
            session, "device-attacker@example.com", "device-attacker"
        )
        await client.post(
            "/api/v1/devices",
            json={"token": "victim-phone", "platform": "ios"},
            headers=_headers(owner_token),
        )

        response = await client.delete(
            "/api/v1/devices/victim-phone",
            headers=_headers(attacker_token),
        )

        assert response.status_code == 204, response.text
        with system_context():
            still_there = await DeviceTokenRepository(session).for_users([owner.id])
        assert [row.token for row in still_there] == ["victim-phone"]

    @pytest.mark.anyio
    async def test_signing_back_in_reclaims_the_same_row(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        """Sign out, sign in, and the install is addressable again."""
        user, token = await _account(
            session, "device-return@example.com", "device-return"
        )
        first = await client.post(
            "/api/v1/devices",
            json={"token": "token-return", "platform": "android"},
            headers=_headers(token),
        )
        await client.delete(
            "/api/v1/devices/token-return",
            headers=_headers(token),
        )

        again = await client.post(
            "/api/v1/devices",
            json={"token": "token-return", "platform": "android"},
            headers=_headers(token),
        )

        assert again.status_code == 200, again.text
        # The same row, undeleted, rather than a second one the unique
        # constraint would have refused.
        assert again.json()["id"] == first.json()["id"]
        with system_context():
            live = await DeviceTokenRepository(session).for_users([user.id])
        assert [row.token for row in live] == ["token-return"]


@pytest.mark.usefixtures("override_get_async_session")
class TestSendAudience:
    """Tests for the query the sender runs."""

    @pytest.mark.anyio
    async def test_collects_every_install_of_every_recipient(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        """One query for the page, and a person's phone and tablet both count."""
        first_user, first_token = await _account(
            session, "device-fan-one@example.com", "device-fan-one"
        )
        second_user, second_token = await _account(
            session, "device-fan-two@example.com", "device-fan-two"
        )
        for bearer, value in (
            (first_token, "phone"),
            (first_token, "tablet"),
            (second_token, "their-phone"),
        ):
            await client.post(
                "/api/v1/devices",
                json={"token": value, "platform": "ios"},
                headers=_headers(bearer),
            )

        with system_context():
            found = await DeviceTokenRepository(session).for_users(
                [first_user.id, second_user.id]
            )

        assert sorted(row.token for row in found) == [
            "phone",
            "tablet",
            "their-phone",
        ]

    @pytest.mark.anyio
    async def test_nobody_to_notify_costs_no_query(self, session) -> None:
        """An event with no audience is the common case for a new account."""
        with system_context():
            assert await DeviceTokenRepository(session).for_users([]) == []
