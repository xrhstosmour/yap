"""Tests for tenant API endpoints."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from typing import Literal

import pytest
from httpx import ASGITransport
from httpx import AsyncClient

from app.main import app
from app.models.user import User


@pytest.fixture
def anyio_backend() -> Literal["asyncio"]:
    return "asyncio"


@pytest.fixture
async def client() -> Generator[AsyncClient, Any]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.anyio
async def test_list_tenants_unauthorized(client: AsyncClient) -> None:
    response = await client.get("/api/v1/tenants")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_create_tenant_unauthorized(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/tenants",
        json={"name": "Acme Corp", "slug": "acme"},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_get_tenant_not_found(client: AsyncClient) -> None:
    response = await client.get("/api/v1/tenants/00000000-0000-0000-0000-000000000001")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_update_tenant_unauthorized(client: AsyncClient) -> None:
    response = await client.patch(
        "/api/v1/tenants/00000000-0000-0000-0000-000000000001",
        json={"name": "Updated"},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_delete_tenant_unauthorized(client: AsyncClient) -> None:
    response = await client.delete(
        "/api/v1/tenants/00000000-0000-0000-0000-000000000001"
    )
    assert response.status_code == 401


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_list_tenants_authenticated_non_admin(
    client: AsyncClient, session
) -> None:
    from app.schemas.auth import RegisterRequest
    from app.services.auth_service import AuthService

    service = AuthService(session)
    user = await service.register(
        RegisterRequest(email="tenant-test@example.com", password="password123")
    )
    tokens = service.create_tokens(user)
    await session.commit()

    response = await client.get(
        "/api/v1/tenants",
        headers={"Authorization": f"Bearer {tokens.access_token}"},
    )
    assert response.status_code == 403


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_crud_tenant_as_admin(client: AsyncClient, session) -> None:
    from app.services.auth_service import AuthService

    service = AuthService(session)
    admin = User(
        email="admin-tenant@example.com",
        hashed_password="hash",
        is_superuser=True,
        is_active=True,
    )
    session.add(admin)
    await session.commit()
    tokens = service.create_tokens(admin)

    response = await client.post(
        "/api/v1/tenants",
        json={"name": "Test", "slug": "test-corp"},
        headers={"Authorization": f"Bearer {tokens.access_token}"},
    )
    assert response.status_code == 201
    tenant_id = response.json()["id"]

    response = await client.get(
        f"/api/v1/tenants/{tenant_id}",
        headers={"Authorization": f"Bearer {tokens.access_token}"},
    )
    assert response.status_code == 200
    assert response.json()["slug"] == "test-corp"

    response = await client.patch(
        f"/api/v1/tenants/{tenant_id}",
        json={"name": "Updated Corp"},
        headers={"Authorization": f"Bearer {tokens.access_token}"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Corp"

    response = await client.delete(
        f"/api/v1/tenants/{tenant_id}",
        headers={"Authorization": f"Bearer {tokens.access_token}"},
    )
    assert response.status_code == 204


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_cannot_delete_system_tenant(client: AsyncClient, session) -> None:
    from app.core import SYSTEM_TENANT_ID
    from app.models.tenant import Tenant
    from app.services.auth_service import AuthService

    service = AuthService(session)

    tenant = Tenant(id=SYSTEM_TENANT_ID, name="System", slug="system")
    session.add(tenant)
    await session.flush()

    admin = User(
        email="admin-sys@example.com",
        hashed_password="hash",
        is_superuser=True,
        is_active=True,
    )
    session.add(admin)
    await session.commit()
    tokens = service.create_tokens(admin)

    response = await client.delete(
        f"/api/v1/tenants/{SYSTEM_TENANT_ID}",
        headers={"Authorization": f"Bearer {tokens.access_token}"},
    )
    assert response.status_code == 400
    assert "system tenant" in response.json()["detail"].lower()


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_duplicate_slug_rejected(client: AsyncClient, session) -> None:
    from app.services.auth_service import AuthService

    service = AuthService(session)
    admin = User(
        email="admin-dup@example.com",
        hashed_password="hash",
        is_superuser=True,
        is_active=True,
    )
    session.add(admin)
    await session.commit()
    tokens = service.create_tokens(admin)

    await client.post(
        "/api/v1/tenants",
        json={"name": "A", "slug": "duplicate-me"},
        headers={"Authorization": f"Bearer {tokens.access_token}"},
    )
    response = await client.post(
        "/api/v1/tenants",
        json={"name": "B", "slug": "duplicate-me"},
        headers={"Authorization": f"Bearer {tokens.access_token}"},
    )
    assert response.status_code == 400
