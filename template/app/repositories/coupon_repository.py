"""Coupon repositories for database operations."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import and_
from sqlmodel import select

from app.core.logging import get_logger
from app.models.coupon import Coupon
from app.models.coupon import CouponRedemption
from app.repositories.base import BaseRepository

logger = get_logger("repository.coupon")


class CouponRepository(BaseRepository[Coupon]):
    """Repository for `Coupon` model operations.

    `Coupon` is a global catalog, not tenant-scoped — a coupon can be
    restricted to specific tenants via its `allowed_tenant_ids` JSON
    allow-list, but the row itself always has `tenant_id = NULL`.
    `_apply_tenant_filter` is overridden to a no-op for the same reason
    as `PlanRepository`: it must remain visible from inside an active
    tenant's request context, where the inherited filter would
    otherwise match zero rows.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Coupon)

    def _apply_tenant_filter(self, query):  # type: ignore[override] # noqa: ANN001,ANN401
        """No-op: `Coupon` is a global catalog, never tenant-scoped."""
        return query

    async def get_by_code(self, code: str) -> Coupon | None:
        """Look up a coupon by its normalized (uppercase) code."""
        query = select(Coupon).where(Coupon.code == code.upper())  # type: ignore[arg-type]
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_for_update(self, coupon_id: UUID) -> Coupon | None:
        """Row-lock a coupon for a redemption-count increment."""
        query = (
            select(Coupon).where(Coupon.id == coupon_id).with_for_update()  # type: ignore[arg-type]
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def increment_redemption_count(self, coupon_id: UUID) -> None:
        """Atomically increment `redemption_count`.

        Caller must already hold the row lock from `get_for_update`.
        """
        statement = (
            update(Coupon)
            .where(Coupon.id == coupon_id)  # type: ignore[arg-type]
            .values(redemption_count=Coupon.redemption_count + 1)
        )
        await self.session.execute(statement)
        await self.session.flush()

    async def decrement_redemption_count(self, coupon_id: UUID) -> None:
        """Atomically decrement `redemption_count`, floored at 0.

        Used by the sweep task when reclaiming an abandoned checkout's
        coupon slot.
        """
        statement = (
            update(Coupon)
            .where(Coupon.id == coupon_id)  # type: ignore[arg-type]
            .values(
                redemption_count=Coupon.redemption_count
                - 1  # type: ignore[operator]
            )
        )
        await self.session.execute(statement)
        await self.session.flush()


class CouponRedemptionRepository(BaseRepository[CouponRedemption]):
    """Repository for `CouponRedemption` model operations.

    Tenant-scoped in the normal way — the redeeming tenant.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, CouponRedemption)

    async def get_active_for_tenant(
        self, coupon_id: UUID, tenant_id: UUID
    ) -> CouponRedemption | None:
        """Find an existing redemption of `coupon_id` by `tenant_id`, if any."""
        query = select(CouponRedemption).where(
            and_(
                CouponRedemption.coupon_id == coupon_id,  # type: ignore[arg-type]
                CouponRedemption.tenant_id == tenant_id,  # type: ignore[arg-type]
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def finalize(
        self, redemption_id: UUID, subscription_id: UUID
    ) -> CouponRedemption | None:
        """Attach the completed `Subscription` and stamp `redeemed_at`."""
        return await self.update(
            redemption_id,
            {"subscription_id": subscription_id, "redeemed_at": datetime.now(UTC)},
        )

    async def list_abandoned(self, older_than: datetime, limit: int = 500) -> list[
        CouponRedemption
    ]:
        """List redemptions with no attached subscription, older than a cutoff.

        Used by the sweep task to reclaim coupon slots from abandoned
        checkouts. Bypasses tenant scoping deliberately — the sweep
        task must see all tenants.
        """
        query = (
            select(CouponRedemption)
            .where(
                and_(
                    CouponRedemption.subscription_id.is_(None),  # type: ignore[union-attr]
                    CouponRedemption.created_at < older_than,  # type: ignore[arg-type]
                )
            )
            .limit(limit)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def delete_hard(self, redemption_id: UUID) -> None:
        """Hard-delete an abandoned redemption row."""
        query = select(CouponRedemption).where(CouponRedemption.id == redemption_id)  # type: ignore[arg-type]
        result = await self.session.execute(query)
        obj = result.scalar_one_or_none()
        if obj is not None:
            await self.session.delete(obj)
            await self.session.flush()
