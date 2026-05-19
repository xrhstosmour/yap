"""Integration tests for `Users` API endpoints."""

from typing import cast
from uuid import uuid7

import pytest
from httpx import ASGITransport
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.core.security import generate_password_hash
from app.main import app
from app.schemas.auth import RegisterRequest
from app.services.auth_service import AuthService


def _auth_service(session: AsyncSession) -> AuthService:
    return AuthService(cast(AsyncSession, session))


async def _create_superuser(auth_service: AuthService):
    return await auth_service.user_repository.create_user(
        email=f"admin-{uuid7()}@example.com",
        password_hash=generate_password_hash("password123"),
        tenant_id=None,
        is_superuser=True,
    )


@pytest.fixture(name="client")
def client_fixture() -> AsyncClient:
    """Create a test client."""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestUsersList:
    """Tests for GET /api/v1/users."""

    @pytest.mark.usefixtures("override_get_async_session")
    async def test_list_users_unauthenticated(self, client: AsyncClient):
        """List users should require authentication."""
        response = await client.get("/api/v1/users")

        assert response.status_code == 401

    @pytest.mark.usefixtures("override_get_async_session")
    async def test_list_users_authenticated(self, client: AsyncClient, session):
        """List users should succeed with authentication."""
        auth_service = _auth_service(session)
        await auth_service.register(
            RegisterRequest(email="user@example.com", password="password123")
        )
        superuser = await _create_superuser(auth_service)
        token = create_access_token(subject=superuser.id)

        response = await client.request(
            "GET",
            "/api/v1/users",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )

        assert response.status_code == 200


class TestUsersRead:
    """Tests for GET /api/v1/users/{user_id}."""

    @pytest.mark.usefixtures("override_get_async_session")
    async def test_read_user_by_id(self, client: AsyncClient, session):
        """Read user should return user data."""
        auth_service = _auth_service(session)
        target_user = await auth_service.register(
            RegisterRequest(email="user@example.com", password="password123")
        )
        superuser = await _create_superuser(auth_service)
        token = create_access_token(subject=superuser.id)

        response = await client.get(
            f"/api/v1/users/{target_user.id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "user@example.com"

    @pytest.mark.usefixtures("override_get_async_session")
    async def test_read_user_not_found(self, client: AsyncClient, session):
        """Read non-existent user should return 404."""
        auth_service = _auth_service(session)
        superuser = await _create_superuser(auth_service)
        token = create_access_token(subject=superuser.id)

        response = await client.get(
            f"/api/v1/users/{str(uuid7())}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404


class TestUsersUpdate:
    """Tests for PATCH /api/v1/users/{user_id}."""

    @pytest.mark.usefixtures("override_get_async_session")
    async def test_update_user(self, client: AsyncClient, session):
        """Update user should modify user data."""
        auth_service = _auth_service(session)
        target_user = await auth_service.register(
            RegisterRequest(email="user@example.com", password="password123")
        )
        superuser = await _create_superuser(auth_service)
        token = create_access_token(subject=superuser.id)

        response = await client.patch(
            f"/api/v1/users/{target_user.id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"full_name": "Updated Name"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["full_name"] == "Updated Name"


class TestUsersDelete:
    """Tests for DELETE /api/v1/users/{user_id}."""

    @pytest.mark.usefixtures("override_get_async_session")
    async def test_delete_user(self, client: AsyncClient, session):
        """Delete user should remove user."""
        auth_service = _auth_service(session)
        target_user = await auth_service.register(
            RegisterRequest(email="user@example.com", password="password123")
        )
        superuser = await _create_superuser(auth_service)
        token = create_access_token(subject=superuser.id)

        response = await client.delete(
            f"/api/v1/users/{target_user.id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 204
