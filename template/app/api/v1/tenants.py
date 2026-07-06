"""Tenant management API routes (admin only)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from fastapi.requests import Request

from app.core.logging import get_logger
from app.core.pagination import PAGINATION_HEADERS_SPEC
from app.core.pagination import PaginatedResponse
from app.dependencies import SessionDependency
from app.dependencies import SuperuserUser
from app.repositories.audit_repository import AuditLogRepository
from app.schemas.tenant import TenantCreate
from app.schemas.tenant import TenantListParameters
from app.schemas.tenant import TenantListResponse
from app.schemas.tenant import TenantResponse
from app.schemas.tenant import TenantUpdate
from app.services.tenant_service import TenantService
from app.services.tenant_service import TenantServiceError
from app.services.tenant_service import TenantSlugAlreadyExistsError

router = APIRouter(prefix="/tenants", tags=["Tenants"])
logger = get_logger("api.tenants")


def get_tenant_service(session: SessionDependency) -> TenantService:
    return TenantService(session, audit_repository=AuditLogRepository(session))


TenantServiceDependency = Annotated[TenantService, Depends(get_tenant_service)]


@router.get(
    "",
    response_model=TenantListResponse,
    responses=PAGINATION_HEADERS_SPEC,  # type: ignore[arg-type]
    summary="List tenants",
    description="List all tenants with pagination and filtering. Admin only.",
)
async def list_tenants(
    parameters: Annotated[TenantListParameters, Depends()],
    current_user: SuperuserUser,
    service: TenantServiceDependency,
    request: Request,
) -> PaginatedResponse:
    tenants, total = await service.list_tenants(
        skip=parameters.skip,
        limit=parameters.limit,
        is_active=parameters.is_active,
        search=parameters.search,
        sort_by=parameters.sort_by or "name",
        sort_order=parameters.sort_order,
    )

    page = (parameters.skip // parameters.limit) + 1 if parameters.limit > 0 else 1
    pages = (total + parameters.limit - 1) // parameters.limit if parameters.limit > 0 else 1

    return PaginatedResponse(
        content=TenantListResponse(
            data=[TenantResponse.model_validate(t) for t in tenants],
            total=total,
            page=page,
            page_size=parameters.limit,
            pages=pages,
        ).model_dump(),
        total=total,
        skip=parameters.skip,
        limit=parameters.limit,
        request=request,
    )


@router.get(
    "/{tenant_id}",
    response_model=TenantResponse,
    summary="Get tenant",
    description="Get a specific tenant by ID. Admin only.",
)
async def get_tenant(
    tenant_id: UUID,
    current_user: SuperuserUser,
    service: TenantServiceDependency,
) -> TenantResponse:
    tenant = await service.get_by_id(tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
        )
    return TenantResponse.model_validate(tenant)


@router.post(
    "",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create tenant",
    description="Create a new tenant. Admin only.",
)
async def create_tenant(
    data: TenantCreate,
    current_user: SuperuserUser,
    service: TenantServiceDependency,
) -> TenantResponse:
    try:
        tenant = await service.create(data, created_by=current_user.id)
        return TenantResponse.model_validate(tenant)
    except (ValueError, TenantServiceError, TenantSlugAlreadyExistsError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@router.patch(
    "/{tenant_id}",
    response_model=TenantResponse,
    summary="Update tenant",
    description="Update a tenant. Admin only.",
)
async def update_tenant(
    tenant_id: UUID,
    data: TenantUpdate,
    current_user: SuperuserUser,
    service: TenantServiceDependency,
) -> TenantResponse:
    try:
        tenant = await service.update(tenant_id, data, updated_by=current_user.id)
    except (ValueError, TenantServiceError, TenantSlugAlreadyExistsError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e

    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
        )
    return TenantResponse.model_validate(tenant)


@router.delete(
    "/{tenant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete tenant",
    description="Delete a tenant. Admin only.",
)
async def delete_tenant(
    tenant_id: UUID,
    current_user: SuperuserUser,
    service: TenantServiceDependency,
) -> None:
    try:
        deleted = await service.delete(tenant_id, deleted_by=current_user.id)
    except (ValueError, TenantServiceError, TenantSlugAlreadyExistsError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
        )
