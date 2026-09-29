"""Tests for which kinds of push an account wants.

The load-bearing claim is that the preference is read when the push is
sent, not when it is queued. A send scheduled before somebody turned a
kind off still has to be dropped, or switching notifications off leaves a
backlog arriving for as long as the queue is deep.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from typing import Literal

import pytest
from httpx import ASGITransport
from httpx import AsyncClient

from app.core.tenant import system_context
from app.main import app
from app.models.device_token import DevicePlatform
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.device_token_repository import DeviceTokenRepository
from app.repositories.notification_preference_repository import (
    NotificationPreferenceRepository,
)


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
class TestNotificationPreferencesApi:
    """Tests for GET and PUT /notification-preferences."""

    @pytest.mark.anyio
    async def test_a_new_account_has_answered_nothing(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        """Absent means on, so nothing needs writing to start delivering."""
        _, token = await _account(session, "preference-new@example.com", "pref-new")

        response = await client.get(
            "/api/v1/notification-preferences",
            headers=_headers(token),
        )

        assert response.status_code == 200, response.text
        assert response.json()["preferences"] == {}

    @pytest.mark.anyio
    async def test_setting_one_kind_returns_them_all(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        """One response redraws the whole screen."""
        _, token = await _account(session, "preference-set@example.com", "pref-set")

        await client.put(
            "/api/v1/notification-preferences",
            json={"kind": "invite", "is_enabled": False},
            headers=_headers(token),
        )
        response = await client.put(
            "/api/v1/notification-preferences",
            json={"kind": "follow", "is_enabled": True},
            headers=_headers(token),
        )

        assert response.status_code == 200, response.text
        assert response.json()["preferences"] == {"invite": False, "follow": True}

    @pytest.mark.anyio
    async def test_answering_the_same_kind_twice_keeps_one_answer(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        """A repeated tap is an update, never a second row nobody can order."""
        _, token = await _account(session, "preference-twice@example.com", "pref-two")

        for is_enabled in (False, True, False):
            response = await client.put(
                "/api/v1/notification-preferences",
                json={"kind": "invite", "is_enabled": is_enabled},
                headers=_headers(token),
            )
            assert response.status_code == 200, response.text

        assert response.json()["preferences"] == {"invite": False}

    @pytest.mark.anyio
    async def test_one_account_cannot_read_another(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        """The answers are per account, and nothing takes a user id."""
        _, first = await _account(session, "preference-a@example.com", "pref-a")
        _, second = await _account(session, "preference-b@example.com", "pref-b")

        await client.put(
            "/api/v1/notification-preferences",
            json={"kind": "invite", "is_enabled": False},
            headers=_headers(first),
        )
        response = await client.get(
            "/api/v1/notification-preferences",
            headers=_headers(second),
        )

        assert response.status_code == 200, response.text
        assert response.json()["preferences"] == {}


@pytest.mark.usefixtures("override_get_async_session")
class TestPreferencesAtSendTime:
    """Tests for the check on the delivery path."""

    @pytest.mark.anyio
    async def test_a_muted_kind_drops_the_recipient(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        """Read when the push is sent, so a queued one is dropped too."""
        user, token = await _account(session, "send-muted@example.com", "send-muted")
        with system_context():
            devices = DeviceTokenRepository(session)
            await devices.register(user.id, "token-muted", DevicePlatform.IOS)
            await session.commit()

            # Queued while the kind was still on, sent after it went off.
            before = await devices.for_users([user.id], kind="invite")
            assert len(before) == 1

            await NotificationPreferenceRepository(session).record(
                user.id, "invite", is_enabled=False
            )
            await session.commit()

            assert await devices.for_users([user.id], kind="invite") == []
            # Another kind is untouched, and so is a send that names none,
            # which is what anything unmutable uses.
            assert len(await devices.for_users([user.id], kind="follow")) == 1
            assert len(await devices.for_users([user.id])) == 1

    @pytest.mark.anyio
    async def test_turning_a_kind_back_on_delivers_again(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        """Off is not a one-way door."""
        user, _ = await _account(session, "send-back@example.com", "send-back")
        with system_context():
            devices = DeviceTokenRepository(session)
            await devices.register(user.id, "token-back", DevicePlatform.ANDROID)
            preferences = NotificationPreferenceRepository(session)
            await preferences.record(user.id, "invite", is_enabled=False)
            await session.commit()
            assert await devices.for_users([user.id], kind="invite") == []

            await preferences.record(user.id, "invite", is_enabled=True)
            await session.commit()

            assert len(await devices.for_users([user.id], kind="invite")) == 1
