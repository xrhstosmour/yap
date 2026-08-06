"""Payment and PaymentMethod repositories."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from sqlmodel import update

from app.core.logging import get_logger
from app.models.payment import Payment
from app.models.payment import PaymentMethod
from app.repositories.base import BaseRepository

logger = get_logger("repository.payment")


class PaymentRepository(BaseRepository[Payment]):
    """Repository for `Payment` model operations.

    Rows are populated exclusively from webhook handlers — see
    `app.models.payment.Payment` docstring. No method here accepts
    arbitrary client input; every write path is a webhook-driven event
    handler.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Payment)

    async def get_by_stripe_payment_intent_id(
        self, stripe_payment_intent_id: str
    ) -> Payment | None:
        query = select(Payment).where(
            Payment.stripe_payment_intent_id == stripe_payment_intent_id  # type: ignore[arg-type]
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_stripe_charge_id(self, stripe_charge_id: str) -> Payment | None:
        query = select(Payment).where(
            Payment.stripe_charge_id == stripe_charge_id  # type: ignore[arg-type]
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def list_for_tenant(
        self, tenant_id: UUID, skip: int = 0, limit: int = 20
    ) -> tuple[list[Payment], int]:
        payments, total = await self.list(
            skip=skip,
            limit=limit,
            filters={"tenant_id": tenant_id},
            sort_by="created_at",
            sort_order="desc",
        )
        return list(payments), total


class PaymentMethodRepository(BaseRepository[PaymentMethod]):
    """Repository for `PaymentMethod` model operations."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, PaymentMethod)

    async def get_by_stripe_payment_method_id(
        self, stripe_payment_method_id: str
    ) -> PaymentMethod | None:
        query = select(PaymentMethod).where(
            PaymentMethod.stripe_payment_method_id == stripe_payment_method_id  # type: ignore[arg-type]
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def list_for_tenant(self, tenant_id: UUID) -> list[PaymentMethod]:
        payment_methods, _ = await self.list(
            limit=100, filters={"tenant_id": tenant_id}
        )
        return list(payment_methods)

    async def clear_default_for_tenant(self, tenant_id: UUID) -> None:
        """Unset `is_default` on every payment method for a tenant.

        Called before setting a new default, so at most one payment
        method is ever marked default per tenant.
        """
        statement = (
            update(PaymentMethod)
            .where(PaymentMethod.tenant_id == tenant_id)  # type: ignore[arg-type]
            .values(is_default=False)
        )
        await self.session.execute(statement)
        await self.session.flush()
