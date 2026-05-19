"""API Key management routes.

This module provides API key management endpoints for
creating and managing API keys.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import status

from app.core.logging import get_logger
from app.dependencies import CurrentUser
from app.dependencies import SessionDep
from app.schemas.api_key import APIKeyCreate
from app.schemas.api_key import APIKeyCreateResponse
from app.schemas.api_key import APIKeyListParams
from app.schemas.api_key import APIKeyListResponse
from app.schemas.api_key import APIKeyResponse
from app.schemas.api_key import APIKeyUpdate
from app.services.api_key_service import APIKeyService

router = APIRouter(prefix="/api-keys", tags=["API Keys"])
logger = get_logger("api.api_keys")


@router.post(
    "",
    response_model=APIKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create API key",
    description="Create a new API key. The full key is only shown once.",
)
async def create_api_key(
    data: APIKeyCreate,
    current_user: CurrentUser,
    session: SessionDep,
) -> APIKeyCreateResponse:
    """Create a new API key.

    Returns the full API key which should be saved immediately
    as it cannot be retrieved later.
    """
    if not current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User must belong to a tenant",
        )

    service = APIKeyService(session)
    api_key, raw_key = await service.create(
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
        data=data,
    )

    return APIKeyCreateResponse(
        id=api_key.id,
        key_id=api_key.key_id,
        api_key=raw_key,
        name=api_key.name,
        scopes=api_key.scopes,
        expires_at=api_key.expires_at,
        created_at=api_key.created_at,
    )


@router.get(
    "",
    response_model=APIKeyListResponse,
    summary="List API keys",
    description="List all API keys for the current user.",
)
async def list_api_keys(
    params: APIKeyListParams,
    current_user: CurrentUser,
    session: SessionDep,
) -> APIKeyListResponse:
    """List current user's API keys."""
    service = APIKeyService(session)

    keys, total = await service.list_for_user(
        user_id=current_user.id,
        skip=params.skip,
        limit=params.limit,
    )

    page = (params.skip // params.limit) + 1 if params.limit > 0 else 1
    pages = (total + params.limit - 1) // params.limit if params.limit > 0 else 1

    return APIKeyListResponse(
        items=[APIKeyResponse.model_validate(k) for k in keys],
        total=total,
        page=page,
        page_size=params.limit,
        pages=pages,
    )


@router.patch(
    "/{key_id}",
    response_model=APIKeyResponse,
    summary="Update API key",
    description="Update an API key's name, description, or scopes.",
)
async def update_api_key(
    key_id: UUID,
    data: APIKeyUpdate,
    current_user: CurrentUser,
    session: SessionDep,
) -> APIKeyResponse:
    """Update an API key."""
    if not current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User must belong to a tenant",
        )

    service = APIKeyService(session)
    api_key = await service.update(
        key_id=key_id,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
        data=data,
    )

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )

    return APIKeyResponse.model_validate(api_key)


@router.delete(
    "/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete API key",
    description="Delete an API key permanently.",
)
async def delete_api_key(
    key_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
) -> None:
    """Delete an API key."""
    if not current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User must belong to a tenant",
        )

    service = APIKeyService(session)
    deleted = await service.delete(
        key_id=key_id,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
    )

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )


@router.post(
    "/{key_id}/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke API key",
    description="Revoke an API key (soft delete).",
)
async def revoke_api_key(
    key_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
) -> None:
    """Revoke an API key."""
    if not current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User must belong to a tenant",
        )

    service = APIKeyService(session)
    revoked = await service.revoke(
        key_id=key_id,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
    )

    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )
