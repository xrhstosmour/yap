"""Plan repository for database operations."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.logging import get_logger
from app.models.plan import Plan
from app.repositories.base import BaseRepository

logger = get_logger("repository.plan")


class PlanRepository(BaseRepository[Plan]):
    """Repository for `Plan` model operations.

    `Plan` is a global catalog, not tenant-scoped: `tenant_id` is always
    `NULL` on every row. `BaseRepository._apply_tenant_filter` filters on
    `WHERE tenant_id = :tenant_id` whenever a tenant context is active,
    which would match zero rows against an always-`NULL` column — plans
    must be visible from *inside* an authenticated tenant's request
    context (e.g. listing plans on a checkout page), so that filter is
    overridden to a no-op here rather than relying on it accidentally
    behaving correctly.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Plan)

    def _apply_tenant_filter(self, query):  # type: ignore[override] # noqa: ANN001,ANN401
        """No-op: `Plan` is a global catalog, never tenant-scoped."""
        return query

    async def get_by_name(self, name: str) -> Plan | None:
        query = select(Plan).where(Plan.name == name)  # type: ignore[arg-type]
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def list_active(self) -> list[Plan]:
        """List all active (non-deleted) plans."""
        plans, _ = await self.list(limit=1000, filters={"is_active": True})
        return list(plans)
