"""Tests for `InvoiceRepository`'s row-locked sequential invoice numbering."""

from __future__ import annotations

from datetime import date

import pytest

from app.models.invoice import InvoiceStatus
from app.models.tenant import Tenant
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.invoice_repository import InvoiceSequenceRepository


class TestInvoiceSequentialNumbering:
    @pytest.fixture
    def anyio_backend(self) -> str:
        return "asyncio"

    async def test_first_number_in_a_new_series_is_one(self, session) -> None:
        repository = InvoiceSequenceRepository(session)
        number = await repository.next_number("2099")
        assert number == 1

    async def test_numbers_increment_sequentially_with_no_gaps(self, session) -> None:
        repository = InvoiceSequenceRepository(session)
        numbers = [await repository.next_number("2098") for _ in range(5)]
        assert numbers == [1, 2, 3, 4, 5]

    async def test_series_are_independent(self, session) -> None:
        repository = InvoiceSequenceRepository(session)
        assert await repository.next_number("series-a") == 1
        assert await repository.next_number("series-b") == 1
        assert await repository.next_number("series-a") == 2

    def test_format_invoice_number(self) -> None:
        assert (
            InvoiceSequenceRepository.format_invoice_number("2026", 123)
            == "INV-2026-000123"
        )

    async def test_issue_invoice_mints_sequential_number(self, session) -> None:
        tenant = Tenant(name="Invoice Test Org", slug="invoice-test-org")
        session.add(tenant)
        await session.commit()

        repository = InvoiceRepository(session)
        invoice = await repository.issue_invoice(
            "2097",
            {
                "tenant_id": tenant.id,
                "status": InvoiceStatus.PAID,
                "issue_date": date.today(),
                "amount_due_cents": 2900,
            },
        )
        await session.commit()

        assert invoice.invoice_number == "INV-2097-000001"

        second = await repository.issue_invoice(
            "2097",
            {
                "tenant_id": tenant.id,
                "status": InvoiceStatus.PAID,
                "issue_date": date.today(),
                "amount_due_cents": 2900,
            },
        )
        assert second.invoice_number == "INV-2097-000002"
