"""Tenant repository for data access."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select
from sqlmodel import func
from sqlmodel import select

from app.models.tenant import Tenant
from app.repositories.base import BaseRepository


class TenantRepository(BaseRepository[Tenant]):
    """Repository for Tenant model operations."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Tenant)

    def _apply_tenant_filter(self, query: Any) -> Any:  # noqa: ANN401
        """Tenant rows are not scoped by another tenant, no-op.

        `Tenant` inherits `BaseModel.tenant_id` for column-shape
        consistency, but the column is never populated: a tenant is not
        owned by another tenant. Filtering on it would make every
        `get`/`update`/`delete`/`exists` inherited from `BaseRepository`
        match nothing.
        """
        return query

    async def get_by_slug(self, slug: str) -> Tenant | None:
        """Get a tenant by its slug, ignoring soft-deleted rows."""
        result = await self.session.execute(
            select(Tenant).where(
                Tenant.slug == slug,  # type: ignore[arg-type]
                Tenant.deleted_at.is_(None),  # type: ignore[union-attr]
            )
        )
        return result.scalar_one_or_none()

    async def slug_exists(self, slug: str, exclude_id: UUID | None = None) -> bool:
        """Check whether a slug is taken by a non-deleted tenant.

        Ignoring soft-deleted rows here (and in `get_by_slug`) matters:
        without it, a deleted tenant's slug stays permanently reserved
        and can never be reused by a new tenant.
        """
        query = select(Tenant.id).where(
            Tenant.slug == slug,  # type: ignore[arg-type,call-overload]
            Tenant.deleted_at.is_(None),  # type: ignore[union-attr]
        )
        if exclude_id:
            query = query.where(Tenant.id != exclude_id)  # type: ignore[arg-type]
        result = await self.session.execute(query)
        return result.scalar_one_or_none() is not None

    async def list_tenants(
        self,
        skip: int = 0,
        limit: int = 20,
        is_active: bool | None = None,
        search: str | None = None,
        sort_by: str = "name",
        sort_order: str = "asc",
    ) -> tuple[list[Tenant], int]:
        query: Select[tuple[Tenant]] = select(Tenant)
        count_query: Select[tuple[int]] = select(func.count(Tenant.id))  # type: ignore[arg-type]

        query = self._apply_soft_delete_filter(query)
        count_query = self._apply_soft_delete_filter(count_query)  # type: ignore[arg-type]

        if is_active is not None:
            query = query.where(Tenant.is_active == is_active)  # type: ignore[arg-type]
            count_query = count_query.where(Tenant.is_active == is_active)  # type: ignore[arg-type]

        if search:
            pattern = f"%{search}%"
            query = query.where(
                (Tenant.name.ilike(pattern)) | (Tenant.slug.ilike(pattern))  # type: ignore[union-attr,attr-defined]
            )
            count_query = count_query.where(
                (Tenant.name.ilike(pattern)) | (Tenant.slug.ilike(pattern))  # type: ignore[union-attr,attr-defined]
            )

        query = query.offset(skip).limit(limit).order_by(Tenant.name)

        sort_column = getattr(Tenant, sort_by, None)
        if sort_column and sort_order == "desc":
            query = query.order_by(sort_column.desc())  # type: ignore[union-attr]
        elif sort_column:
            query = query.order_by(sort_column)  # type: ignore[arg-type]

        total_result = await self.session.execute(count_query)
        total = total_result.scalar() or 0

        result = await self.session.execute(query)
        tenants = list(result.scalars().all())

        return tenants, total

    async def create_tenant(
        self,
        name: str,
        slug: str,
        settings: dict | None = None,
    ) -> Tenant:
        data: dict[str, object] = {
            "name": name,
            "slug": slug,
            "settings": settings or {},
        }
        return await self.create(data)  # type: ignore[arg-type]

    async def update_tenant(
        self,
        tenant_id: UUID,
        name: str | None = None,
        slug: str | None = None,
        is_active: bool | None = None,
        settings: dict | None = None,
    ) -> Tenant | None:
        data: dict[str, object] = {}
        if name is not None:
            data["name"] = name
        if slug is not None:
            data["slug"] = slug
        if is_active is not None:
            data["is_active"] = is_active
        if settings is not None:
            data["settings"] = settings
        return await self.update(tenant_id, data)  # type: ignore[arg-type]
