"""Tests for tenant service."""

from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.schemas.tenant import TenantCreate
from app.schemas.tenant import TenantUpdate
from app.services.tenant_service import SYSTEM_TENANT_ID
from app.services.tenant_service import TenantService
from app.services.tenant_service import TenantServiceError
from app.services.tenant_service import TenantSlugAlreadyExistsError


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

    with pytest.raises(TenantSlugAlreadyExistsError, match="already exists"):
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

    with pytest.raises(TenantServiceError, match="Cannot delete the system tenant"):
        import asyncio

        asyncio.run(service.delete(SYSTEM_TENANT_ID))


def test_delete_nonexistent_tenant(service: TenantService) -> None:
    service.tenant_repository.get = AsyncMock(return_value=None)

    import asyncio

    result = asyncio.run(service.delete(UUID("00000000-0000-0000-0000-000000000001")))
    assert result is False


# New tests for updated tenant_service.py


def test_get_tenant_by_id_found(service: TenantService) -> None:
    from app.models.tenant import Tenant

    tenant_id = UUID("00000000-0000-0000-0000-000000000001")
    mock_tenant = Tenant(id=tenant_id, name="Acme", slug="acme")
    service.tenant_repository.get = AsyncMock(return_value=mock_tenant)

    import asyncio

    result = asyncio.run(service.get_by_id(tenant_id))
    assert result is mock_tenant
    service.tenant_repository.get.assert_awaited_once_with(tenant_id)


def test_get_tenant_by_id_not_found(service: TenantService) -> None:
    service.tenant_repository.get = AsyncMock(return_value=None)

    import asyncio

    result = asyncio.run(
        service.get_by_id(UUID("00000000-0000-0000-0000-000000000001"))
    )
    assert result is None


def test_update_tenant_success_with_slug_change(service: TenantService) -> None:
    from app.models.tenant import Tenant

    tenant_id = UUID("00000000-0000-0000-0000-000000000001")
    mock_tenant = Tenant(id=tenant_id, name="Acme", slug="acme")
    updated_tenant = Tenant(id=tenant_id, name="Acme Corp", slug="acme-corp")

    service.tenant_repository.get = AsyncMock(return_value=mock_tenant)
    service.tenant_repository.slug_exists = AsyncMock(return_value=False)
    service.tenant_repository.update_tenant = AsyncMock(return_value=updated_tenant)

    import asyncio

    result = asyncio.run(
        service.update(
            tenant_id,
            TenantUpdate(name="Acme Corp", slug="acme-corp"),
        )
    )
    assert result.name == "Acme Corp"
    assert result.slug == "acme-corp"
    service.tenant_repository.slug_exists.assert_awaited_once_with(
        "acme-corp", exclude_id=tenant_id
    )


def test_update_tenant_success_without_slug_change(service: TenantService) -> None:
    from app.models.tenant import Tenant

    tenant_id = UUID("00000000-0000-0000-0000-000000000001")
    mock_tenant = Tenant(id=tenant_id, name="Acme", slug="acme")
    updated_tenant = Tenant(id=tenant_id, name="Acme Corp", slug="acme")

    service.tenant_repository.get = AsyncMock(return_value=mock_tenant)
    service.tenant_repository.update_tenant = AsyncMock(return_value=updated_tenant)

    import asyncio

    result = asyncio.run(
        service.update(tenant_id, TenantUpdate(name="Acme Corp"))
    )
    assert result.name == "Acme Corp"
    service.tenant_repository.slug_exists.assert_not_called()


def test_update_tenant_duplicate_slug(service: TenantService) -> None:
    from app.models.tenant import Tenant

    tenant_id = UUID("00000000-0000-0000-0000-000000000001")
    mock_tenant = Tenant(id=tenant_id, name="Acme", slug="acme")

    service.tenant_repository.get = AsyncMock(return_value=mock_tenant)
    service.tenant_repository.slug_exists = AsyncMock(return_value=True)

    import asyncio

    with pytest.raises(TenantSlugAlreadyExistsError, match="already exists"):
        asyncio.run(
            service.update(
                tenant_id, TenantUpdate(name="Acme", slug="acme-corp")
            )
        )


def test_update_tenant_not_found(service: TenantService) -> None:
    service.tenant_repository.get = AsyncMock(return_value=None)

    import asyncio

    result = asyncio.run(
        service.update(
            UUID("00000000-0000-0000-0000-000000000001"),
            TenantUpdate(name="Acme"),
        )
    )
    assert result is None


def test_create_triggers_audit_log(service: TenantService) -> None:
    from app.models.audit_log import AuditAction
    from app.models.tenant import Tenant

    mock_tenant = Tenant(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        name="Acme",
        slug="acme",
    )
    created_by = UUID("00000000-0000-0000-0000-0000000000aa")

    service.tenant_repository.slug_exists = AsyncMock(return_value=False)
    service.tenant_repository.create_tenant = AsyncMock(return_value=mock_tenant)
    service.audit_repository = AsyncMock()

    import asyncio

    result = asyncio.run(
        service.create(
            TenantCreate(name="Acme", slug="acme"), created_by=created_by
        )
    )
    assert result is mock_tenant
    service.audit_repository.log_user_action.assert_awaited_once_with(
        action=AuditAction.TENANT_CREATE,
        user_id=created_by,
        tenant_id=mock_tenant.id,
        email=None,
        resource_type="tenant",
        resource_id=str(mock_tenant.id),
        metadata={"name": "Acme", "slug": "acme"},
    )


def test_update_triggers_audit_log(service: TenantService) -> None:
    from app.models.audit_log import AuditAction
    from app.models.tenant import Tenant

    tenant_id = UUID("00000000-0000-0000-0000-000000000001")
    updated_by = UUID("00000000-0000-0000-0000-0000000000bb")
    mock_tenant = Tenant(id=tenant_id, name="Acme", slug="acme")
    updated_tenant = Tenant(id=tenant_id, name="Acme Corp", slug="acme-corp")

    service.tenant_repository.get = AsyncMock(return_value=mock_tenant)
    service.tenant_repository.slug_exists = AsyncMock(return_value=False)
    service.tenant_repository.update_tenant = AsyncMock(return_value=updated_tenant)
    service.audit_repository = AsyncMock()

    import asyncio

    result = asyncio.run(
        service.update(
            tenant_id,
            TenantUpdate(name="Acme Corp", slug="acme-corp"),
            updated_by=updated_by,
        )
    )
    assert result is updated_tenant
    service.audit_repository.log_user_action.assert_awaited_once_with(
        action=AuditAction.TENANT_UPDATE,
        user_id=updated_by,
        tenant_id=tenant_id,
        email=None,
        resource_type="tenant",
        resource_id=str(tenant_id),
        metadata={"name": "Acme Corp", "slug": "acme-corp"},
    )


def test_delete_tenant_success(service: TenantService) -> None:
    from app.models.tenant import Tenant

    tenant_id = UUID("00000000-0000-0000-0000-000000000001")
    mock_tenant = Tenant(id=tenant_id, name="Acme", slug="acme")

    service.tenant_repository.get = AsyncMock(return_value=mock_tenant)
    service.tenant_repository.delete = AsyncMock(return_value=True)

    import asyncio

    result = asyncio.run(service.delete(tenant_id))
    assert result is True
    service.tenant_repository.delete.assert_awaited_once_with(tenant_id)


def test_delete_triggers_audit_log(service: TenantService) -> None:
    from app.models.audit_log import AuditAction
    from app.models.tenant import Tenant

    tenant_id = UUID("00000000-0000-0000-0000-000000000001")
    deleted_by = UUID("00000000-0000-0000-0000-0000000000cc")
    mock_tenant = Tenant(id=tenant_id, name="Acme", slug="acme")

    # Ensure it's not the system tenant
    assert tenant_id != SYSTEM_TENANT_ID

    service.tenant_repository.get = AsyncMock(return_value=mock_tenant)
    service.tenant_repository.delete = AsyncMock(return_value=True)
    service.audit_repository = AsyncMock()

    import asyncio

    result = asyncio.run(service.delete(tenant_id, deleted_by=deleted_by))
    assert result is True
    service.audit_repository.log_user_action.assert_awaited_once_with(
        action=AuditAction.TENANT_DELETE,
        user_id=deleted_by,
        tenant_id=tenant_id,
        email=None,
        resource_type="tenant",
        resource_id=str(tenant_id),
        metadata={"name": "Acme", "slug": "acme"},
    )
