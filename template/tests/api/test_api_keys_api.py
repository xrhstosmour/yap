"""Tests for API key management endpoints."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from typing import Literal

import pytest
from httpx import ASGITransport
from httpx import AsyncClient

from app.main import app
from app.models.tenant import Tenant
from app.models.user import User


@pytest.fixture
def anyio_backend() -> Literal["asyncio"]:
    return "asyncio"


@pytest.fixture
async def client() -> Generator[AsyncClient, Any]:
    """Async HTTP test client for the full app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# Helper: create an authenticated user with a tenant


async def _create_tenant_user_token(session) -> tuple[Tenant, User, str]:
    """Create a tenant, a user in that tenant, and return an access token.

    Returns:
        (tenant, user, access_token)
    """
    from app.services.auth_service import AuthService

    tenant = Tenant(name="Test", slug="test-apikey")
    session.add(tenant)
    await session.flush()

    user = User(
        email="apikey@example.com",
        hashed_password="hash",
        tenant_id=tenant.id,
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    service = AuthService(session)
    tokens = service.create_tokens(user)

    return tenant, user, tokens.access_token


# Tests


class TestAPICreateAPIKey:
    """Tests for POST /api-keys."""

    @pytest.mark.anyio
    @pytest.mark.usefixtures("override_get_async_session")
    async def test_create_api_key_returns_201(self, client: AsyncClient, session) -> None:
        """Authenticated user with a tenant should create an API key and get 201."""
        _, _, token = await _create_tenant_user_token(session)

        response = await client.post(
            "/api/v1/api-keys",
            json={"name": "My Key", "scopes": ["read"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 201
        data = response.json()
        assert "api_key" in data
        assert data["name"] == "My Key"
        assert data["api_key"] != ""


class TestAPIListAPIKeys:
    """Tests for GET /api-keys."""

    @pytest.mark.anyio
    @pytest.mark.usefixtures("override_get_async_session")
    async def test_list_api_keys_returns_paginated_response(self, client: AsyncClient, session) -> None:
        """Authenticated user should be able to list their API keys."""
        _, _, token = await _create_tenant_user_token(session)

        # Create a key first so the list is non-empty.
        await client.post(
            "/api/v1/api-keys",
            json={"name": "Key 1", "scopes": ["read"]},
            headers={"Authorization": f"Bearer {token}"},
        )

        response = await client.get(
            "/api/v1/api-keys",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert data["total"] >= 1
        assert len(data["data"]) >= 1


class TestAPIUpdateAPIKey:
    """Tests for PATCH /api-keys/{key_id}."""

    @pytest.mark.anyio
    @pytest.mark.usefixtures("override_get_async_session")
    async def test_update_api_key_name(self, client: AsyncClient, session) -> None:
        """Authenticated user should be able to rename their API key."""
        _, _, token = await _create_tenant_user_token(session)

        # Create a key.
        create_resp = await client.post(
            "/api/v1/api-keys",
            json={"name": "Original Name", "scopes": ["read"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create_resp.status_code == 201
        key_id = create_resp.json()["id"]  # UUID used in path params

        # Update its name.
        update_resp = await client.patch(
            f"/api/v1/api-keys/{key_id}",
            json={"name": "Updated Name"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["name"] == "Updated Name"


class TestAPIRevokeAPIKey:
    """Tests for POST /api-keys/{key_id}/revoke."""

    @pytest.mark.anyio
    @pytest.mark.usefixtures("override_get_async_session")
    async def test_revoke_api_key_returns_204(self, client: AsyncClient, session) -> None:
        """Authenticated user should be able to revoke their API key."""
        _, _, token = await _create_tenant_user_token(session)

        create_resp = await client.post(
            "/api/v1/api-keys",
            json={"name": "To Revoke", "scopes": ["read"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create_resp.status_code == 201
        key_id = create_resp.json()["id"]  # UUID used in path params

        revoke_resp = await client.post(
            f"/api/v1/api-keys/{key_id}/revoke",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert revoke_resp.status_code == 204


class TestAPIDeleteAPIKey:
    """Tests for DELETE /api-keys/{key_id}."""

    @pytest.mark.anyio
    @pytest.mark.usefixtures("override_get_async_session")
    async def test_delete_api_key_returns_204(self, client: AsyncClient, session) -> None:
        """Authenticated user should be able to delete their API key."""
        _, _, token = await _create_tenant_user_token(session)

        create_resp = await client.post(
            "/api/v1/api-keys",
            json={"name": "To Delete", "scopes": ["read"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create_resp.status_code == 201
        key_id = create_resp.json()["id"]  # UUID used in path params

        delete_resp = await client.delete(
            f"/api/v1/api-keys/{key_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert delete_resp.status_code == 204


class TestAPIUnauthorized:
    """Tests for unauthorized access to API key endpoints."""

    @pytest.mark.anyio
    async def test_create_api_key_no_token_returns_401(self, client: AsyncClient) -> None:
        """POST /api-keys without a token should return 401."""
        response = await client.post(
            "/api/v1/api-keys",
            json={"name": "Key", "scopes": ["read"]},
        )
        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_list_api_keys_no_token_returns_401(self, client: AsyncClient) -> None:
        """GET /api-keys without a token should return 401."""
        response = await client.get("/api/v1/api-keys")
        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_update_api_key_no_token_returns_401(self, client: AsyncClient) -> None:
        """PATCH /api-keys/{id} without a token should return 401."""
        response = await client.patch(
            "/api/v1/api-keys/00000000-0000-0000-0000-000000000001",
            json={"name": "X"},
        )
        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_delete_api_key_no_token_returns_401(self, client: AsyncClient) -> None:
        """DELETE /api-keys/{id} without a token should return 401."""
        response = await client.delete(
            "/api/v1/api-keys/00000000-0000-0000-0000-000000000001",
        )
        assert response.status_code == 401
