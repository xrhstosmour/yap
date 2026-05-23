"""Tests for tenant service."""

from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.schemas.tenant import TenantCreate
from app.services.tenant_service import SYSTEM_TENANT_ID
from app.services.tenant_service import TenantService


@pytest.fixture
def mock_session() -> MagicMock:
    return MagicMock()


@pytest.fixture
def service(mock_session: MagicMock) -> TenantService:
    service = TenantService(mock_session)
    service.tenant_repository = MagicMock()
    return service


def test_create_tenant_duplicate_slug(service: TenantService) -> None:
    service.tenant_repository.slug_exists = AsyncMock(return_value=True)

    with pytest.raises(ValueError, match="already exists"):
        import asyncio

        asyncio.run(service.create(TenantCreate(name="Acme", slug="acme")))


def test_create_tenant_success(service: TenantService) -> None:
    service.tenant_repository.slug_exists = AsyncMock(return_value=False)

    from app.models.tenant import Tenant

    mock_tenant = Tenant(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        name="Acme",
        slug="acme",
    )
    service.tenant_repository.create_tenant = AsyncMock(return_value=mock_tenant)

    import asyncio

    result = asyncio.run(service.create(TenantCreate(name="Acme", slug="acme")))
    assert result.name == "Acme"
    assert result.slug == "acme"


def test_cannot_delete_system_tenant(service: TenantService) -> None:
    from app.models.tenant import Tenant

    mock_tenant = Tenant(id=SYSTEM_TENANT_ID, name="System", slug="system")
    service.tenant_repository.get = AsyncMock(return_value=mock_tenant)

    with pytest.raises(ValueError, match="Cannot delete the system tenant"):
        import asyncio

        asyncio.run(service.delete(SYSTEM_TENANT_ID))


def test_delete_nonexistent_tenant(service: TenantService) -> None:
    service.tenant_repository.get = AsyncMock(return_value=None)

    import asyncio

    result = asyncio.run(service.delete(UUID("00000000-0000-0000-0000-000000000001")))
    assert result is False
