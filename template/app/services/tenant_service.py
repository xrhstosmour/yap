"""Tenant service for business logic."""

from __future__ import annotations

from uuid import UUID

from app.core import SYSTEM_TENANT_ID
from app.core.logging import get_logger
from app.models.audit_log import AuditAction
from app.models.tenant import Tenant
from app.repositories.audit_repository import AuditLogRepository
from app.repositories.tenant_repository import TenantRepository
from app.schemas.tenant import TenantCreate
from app.schemas.tenant import TenantUpdate

logger = get_logger("services.tenant")


class TenantService:
    """Service layer for tenant operations."""

    def __init__(
        self, session, audit_repository: AuditLogRepository | None = None
    ) -> None:
        self.tenant_repository = TenantRepository(session)
        self.audit_repository = audit_repository
        self.session = session

    async def list_tenants(
        self,
        skip: int = 0,
        limit: int = 20,
        is_active: bool | None = None,
        search: str | None = None,
        sort_by: str = "name",
        sort_order: str = "asc",
    ) -> tuple[list[Tenant], int]:
        return await self.tenant_repository.list_tenants(
            skip=skip,
            limit=limit,
            is_active=is_active,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    async def get_by_id(self, tenant_id: UUID) -> Tenant | None:
        return await self.tenant_repository.get(tenant_id)  # type: ignore[no-any-return]

    async def create(
        self, data: TenantCreate, created_by: UUID | None = None
    ) -> Tenant:
        if await self.tenant_repository.slug_exists(data.slug):
            raise ValueError(f"Tenant with slug '{data.slug}' already exists")

        tenant = await self.tenant_repository.create_tenant(
            name=data.name,
            slug=data.slug,
            settings=data.settings,
        )

        if self.audit_repository:
            try:
                await self.audit_repository.log_user_action(
                    action=AuditAction.TENANT_CREATE,
                    user_id=created_by or SYSTEM_TENANT_ID,
                    tenant_id=tenant.id,
                    email=None,
                    resource_type="tenant",
                    resource_id=str(tenant.id),
                    extra_data={"name": tenant.name, "slug": tenant.slug},
                )
            except Exception:
                logger.warning("tenant_audit_log_failed", tenant_id=str(tenant.id))

        logger.info("tenant_created", tenant_id=str(tenant.id), slug=tenant.slug)
        return tenant

    async def update(
        self,
        tenant_id: UUID,
        data: TenantUpdate,
        updated_by: UUID | None = None,
    ) -> Tenant | None:
        tenant = await self.tenant_repository.get(tenant_id)  # type: ignore[no-any-return]
        if not tenant:
            return None

        if data.slug and data.slug != tenant.slug:
            if await self.tenant_repository.slug_exists(
                data.slug, exclude_id=tenant_id
            ):
                raise ValueError(f"Tenant with slug '{data.slug}' already exists")

        result = await self.tenant_repository.update_tenant(
            tenant_id=tenant_id,
            name=data.name,
            slug=data.slug,
            is_active=data.is_active,
            settings=data.settings,
        )

        if result:
            logger.info(
                "tenant_updated",
                tenant_id=str(tenant_id),
                slug=data.slug or tenant.slug,
            )
        return result

    async def delete(self, tenant_id: UUID, deleted_by: UUID | None = None) -> bool:
        tenant = await self.tenant_repository.get(tenant_id)  # type: ignore[no-any-return]
        if not tenant:
            return False

        if tenant.id == SYSTEM_TENANT_ID:
            raise ValueError("Cannot delete the system tenant")

        deleted = await self.tenant_repository.delete(tenant_id)
        logger.info("tenant_deleted", tenant_id=str(tenant_id))
        return deleted
