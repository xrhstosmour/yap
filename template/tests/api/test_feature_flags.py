"""Tests for feature flag API endpoints."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from typing import Literal

import pytest
from httpx import ASGITransport
from httpx import AsyncClient

from app.main import app
from app.models.user import User
from app.models.user import UserRole


@pytest.fixture
def anyio_backend() -> Literal["asyncio"]:
    return "asyncio"


@pytest.fixture
async def client() -> Generator[AsyncClient, Any]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.anyio
async def test_list_feature_flags_unauthorized(client: AsyncClient) -> None:
    response = await client.get("/api/v1/feature-flags")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_create_feature_flag_unauthorized(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/feature-flags",
        json={"name": "new_flag", "state": True},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_get_feature_flag_unauthorized(client: AsyncClient) -> None:
    response = await client.get("/api/v1/feature-flags/some_flag")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_update_feature_flag_unauthorized(client: AsyncClient) -> None:
    response = await client.patch(
        "/api/v1/feature-flags/some_flag",
        json={"description": "Updated"},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_toggle_feature_flag_unauthorized(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/feature-flags/some_flag/toggle",
        json={"state": True},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_delete_feature_flag_unauthorized(client: AsyncClient) -> None:
    response = await client.delete("/api/v1/feature-flags/some_flag")
    assert response.status_code == 401


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_list_feature_flags_authenticated_non_admin(
    client: AsyncClient, session
) -> None:
    from app.schemas.auth import RegisterRequest
    from app.services.auth_service import AuthService

    service = AuthService(session)
    user = await service.register(
        RegisterRequest(email="flag-test@example.com", password="password123")
    )
    tokens = service.create_tokens(user)
    await session.commit()

    response = await client.get(
        "/api/v1/feature-flags",
        headers={"Authorization": f"Bearer {tokens.access_token}"},
    )
    assert response.status_code == 403


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_crud_feature_flag_as_admin(client: AsyncClient, session) -> None:
    from app.services.auth_service import AuthService

    service = AuthService(session)
    admin = User(
        email="admin-flag@example.com",
        hashed_password="hash",
        role=UserRole.SUPERUSER,
        is_active=True,
    )
    session.add(admin)
    await session.commit()
    tokens = service.create_tokens(admin)
    headers = {"Authorization": f"Bearer {tokens.access_token}"}

    response = await client.post(
        "/api/v1/feature-flags",
        json={"name": "crud_flag", "state": False, "description": "A flag"},
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "crud_flag"
    assert body["state"] is False

    response = await client.get("/api/v1/feature-flags/crud_flag", headers=headers)
    assert response.status_code == 200
    assert response.json()["description"] == "A flag"

    response = await client.patch(
        "/api/v1/feature-flags/crud_flag",
        json={"description": "Updated description"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["description"] == "Updated description"

    response = await client.post(
        "/api/v1/feature-flags/crud_flag/toggle",
        json={"state": True},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["state"] is True

    response = await client.get(
        "/api/v1/feature-flags",
        headers=headers,
    )
    assert response.status_code == 200
    names = [flag["name"] for flag in response.json()["data"]]
    assert "crud_flag" in names

    response = await client.delete("/api/v1/feature-flags/crud_flag", headers=headers)
    assert response.status_code == 204

    # delete() is a soft delete, and get_by_name() filters deleted_at, so
    # the flag is no longer resolvable by name and its name is free to reuse.
    response = await client.get("/api/v1/feature-flags/crud_flag", headers=headers)
    assert response.status_code == 404


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_get_feature_flag_not_found(client: AsyncClient, session) -> None:
    from app.services.auth_service import AuthService

    service = AuthService(session)
    admin = User(
        email="admin-flag-404@example.com",
        hashed_password="hash",
        role=UserRole.SUPERUSER,
        is_active=True,
    )
    session.add(admin)
    await session.commit()
    tokens = service.create_tokens(admin)

    response = await client.get(
        "/api/v1/feature-flags/nonexistent_flag",
        headers={"Authorization": f"Bearer {tokens.access_token}"},
    )
    assert response.status_code == 404


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_update_feature_flag_not_found(client: AsyncClient, session) -> None:
    from app.services.auth_service import AuthService

    service = AuthService(session)
    admin = User(
        email="admin-flag-update-404@example.com",
        hashed_password="hash",
        role=UserRole.SUPERUSER,
        is_active=True,
    )
    session.add(admin)
    await session.commit()
    tokens = service.create_tokens(admin)

    response = await client.patch(
        "/api/v1/feature-flags/nonexistent_flag",
        json={"description": "ignored"},
        headers={"Authorization": f"Bearer {tokens.access_token}"},
    )
    assert response.status_code == 404


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_toggle_feature_flag_not_found(client: AsyncClient, session) -> None:
    from app.services.auth_service import AuthService

    service = AuthService(session)
    admin = User(
        email="admin-flag-toggle-404@example.com",
        hashed_password="hash",
        role=UserRole.SUPERUSER,
        is_active=True,
    )
    session.add(admin)
    await session.commit()
    tokens = service.create_tokens(admin)

    response = await client.post(
        "/api/v1/feature-flags/nonexistent_flag/toggle",
        json={"state": True},
        headers={"Authorization": f"Bearer {tokens.access_token}"},
    )
    assert response.status_code == 404


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_delete_feature_flag_not_found(client: AsyncClient, session) -> None:
    from app.services.auth_service import AuthService

    service = AuthService(session)
    admin = User(
        email="admin-flag-delete-404@example.com",
        hashed_password="hash",
        role=UserRole.SUPERUSER,
        is_active=True,
    )
    session.add(admin)
    await session.commit()
    tokens = service.create_tokens(admin)

    response = await client.delete(
        "/api/v1/feature-flags/nonexistent_flag",
        headers={"Authorization": f"Bearer {tokens.access_token}"},
    )
    assert response.status_code == 404


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_duplicate_feature_flag_name_rejected(
    client: AsyncClient, session
) -> None:
    from app.services.auth_service import AuthService

    service = AuthService(session)
    admin = User(
        email="admin-flag-dup@example.com",
        hashed_password="hash",
        role=UserRole.SUPERUSER,
        is_active=True,
    )
    session.add(admin)
    await session.commit()
    tokens = service.create_tokens(admin)
    headers = {"Authorization": f"Bearer {tokens.access_token}"}

    await client.post(
        "/api/v1/feature-flags",
        json={"name": "duplicate_flag", "state": False},
        headers=headers,
    )

    response = await client.post(
        "/api/v1/feature-flags",
        json={"name": "duplicate_flag", "state": False},
        headers=headers,
    )

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]
