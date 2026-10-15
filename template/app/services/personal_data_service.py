"""Collecting every row a person's data export carries.

What to collect is declared in `app.core.personal_data`. This only reads
it: the account row, then each classified column's matching rows, parents
before the children that point into them, every row without its secret
columns.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import Table
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel

from app.core.personal_data import EXPORTED
from app.core.personal_data import SECRET_COLUMN
from app.core.personal_data import USER_TABLE


class PersonalDataError(Exception):
    """The export classification points at a table nothing exports."""


class PersonalDataService:
    """Read a person's rows out of every table that holds them."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def collect(self, user_id: UUID) -> dict[str, Any]:
        """Gather the account and every exported row that leads back to it.

        Args:
            user_id: The account being exported.

        Returns:
            `account`, the person's own row, and `records`, the exported
            rows grouped by table, all JSON ready and without secrets.

        Raises:
            PersonalDataError: When a column points into a table that is
                neither the account nor exported itself.
        """
        tables = SQLModel.metadata.tables
        accounts = await self._rows(tables[USER_TABLE], "id", {user_id})
        identifiers: dict[str, set[Any]] = {USER_TABLE: {user_id}}
        records: dict[str, dict[Any, dict[str, Any]]] = {}

        # A table this project dropped is skipped, so a template entry for
        # it does no harm.
        pending = {
            reference: target
            for reference, target in EXPORTED.items()
            if reference.split(".", 1)[0] in tables
        }
        while pending:
            ready = {
                reference: target
                for reference, target in pending.items()
                if target in identifiers and not self._feeds(target, pending)
            }
            if not ready:
                raise PersonalDataError(
                    f"Nothing exports {sorted(set(pending.values()))}"
                )
            for reference, target in ready.items():
                table_name, column = reference.split(".", 1)
                rows = await self._rows(tables[table_name], column, identifiers[target])
                found = records.setdefault(table_name, {})
                for row in rows:
                    found[row["id"]] = row
                identifiers.setdefault(table_name, set()).update(found)
                del pending[reference]

        return {
            "account": jsonable_encoder(accounts[0]) if accounts else {},
            "records": {
                table_name: jsonable_encoder(list(rows.values()))
                for table_name, rows in sorted(records.items())
            },
        }

    @staticmethod
    def _feeds(table_name: str, pending: dict[str, str]) -> bool:
        """Whether a column still to read adds rows to `table_name`."""
        return any(reference.split(".", 1)[0] == table_name for reference in pending)

    async def _rows(
        self, table: Table, column: str, values: set[Any]
    ) -> list[dict[str, Any]]:
        if not values:
            return []
        statement = select(table).where(table.c[column].in_(values))
        if "deleted_at" in table.c:
            statement = statement.where(table.c.deleted_at.is_(None))
        result = await self.session.execute(statement)
        return [
            {
                name: value
                for name, value in row.items()
                if not SECRET_COLUMN.search(name)
            }
            for row in result.mappings().all()
        ]
