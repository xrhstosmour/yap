"""Invoice repositories, including row-locked sequential invoice numbering."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from sqlmodel import update

from app.core.logging import get_logger
from app.models.invoice import Invoice
from app.models.invoice import InvoiceLineItem
from app.models.invoice import InvoiceSequence
from app.repositories.base import BaseRepository

logger = get_logger("repository.invoice")

_INVOICE_NUMBER_FORMAT = "INV-{series}-{number:06d}"


class InvoiceSequenceRepository(BaseRepository[InvoiceSequence]):
    """Repository for the global, gapless `InvoiceSequence` counter.

    Not tenant-scoped — see the module docstring on
    `app.models.invoice` for why. `_apply_tenant_filter` is overridden
    to a no-op for the same reason as `PlanRepository`/`CouponRepository`.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, InvoiceSequence)

    def _apply_tenant_filter(self, query):  # type: ignore[override] # noqa: ANN001,ANN401
        """No-op: `InvoiceSequence` is a global counter, never tenant-scoped."""
        return query

    async def next_number(self, series: str) -> int:
        """Atomically claim and return the next number in `series`.

        Uses `INSERT ... ON CONFLICT DO NOTHING` to create the sequence
        row on first use of a series, then `SELECT ... FOR UPDATE` to
        lock it before incrementing. Must be called inside the same
        transaction that creates the `Invoice` using the returned
        number, so a rollback of the invoice also rolls back the claim
        — never derived from `COUNT(*)`, which races and can gap.
        """
        insert_statement = (
            pg_insert(InvoiceSequence)
            .values(series=series, next_number=1)
            .on_conflict_do_nothing(index_elements=["series"])
        )
        await self.session.execute(insert_statement)

        query = (
            select(InvoiceSequence)
            .where(InvoiceSequence.series == series)  # type: ignore[arg-type]
            .with_for_update()
        )
        result = await self.session.execute(query)
        sequence = result.scalar_one()

        claimed = sequence.next_number
        await self.session.execute(
            update(InvoiceSequence)
            .where(InvoiceSequence.id == sequence.id)  # type: ignore[arg-type]
            .values(next_number=InvoiceSequence.next_number + 1)
        )
        await self.session.flush()

        return claimed

    @staticmethod
    def format_invoice_number(series: str, number: int) -> str:
        return _INVOICE_NUMBER_FORMAT.format(series=series, number=number)


class InvoiceRepository(BaseRepository[Invoice]):
    """Repository for `Invoice` model operations."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Invoice)
        self.sequence_repository = InvoiceSequenceRepository(session)

    async def issue_invoice(self, series: str, data: dict[str, Any]) -> Invoice:
        """Claim the next sequential number in `series` and create the `Invoice`.

        Must run inside a single DB transaction so the number claim and
        the invoice row are committed (or rolled back) together.
        """
        number = await self.sequence_repository.next_number(series)
        data = dict(data)
        data["invoice_number"] = self.sequence_repository.format_invoice_number(
            series, number
        )
        return await self.create(data)

    async def get_by_stripe_invoice_id(self, stripe_invoice_id: str) -> Invoice | None:
        query = select(Invoice).where(Invoice.stripe_invoice_id == stripe_invoice_id)  # type: ignore[arg-type]
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def list_for_tenant(
        self, tenant_id: UUID, skip: int = 0, limit: int = 20
    ) -> tuple[list[Invoice], int]:
        invoices, total = await self.list(
            skip=skip,
            limit=limit,
            filters={"tenant_id": tenant_id},
            sort_by="issue_date",
            sort_order="desc",
        )
        return list(invoices), total


class InvoiceLineItemRepository(BaseRepository[InvoiceLineItem]):
    """Repository for `InvoiceLineItem` model operations."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, InvoiceLineItem)

    async def list_for_invoice(self, invoice_id: UUID) -> list[InvoiceLineItem]:
        query = select(InvoiceLineItem).where(
            InvoiceLineItem.invoice_id == invoice_id  # type: ignore[arg-type]
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())
