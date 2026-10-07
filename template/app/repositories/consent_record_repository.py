"""Consent record repository.

This module provides the ConsentRecordRepository class for writing consent
answers and reading back the current one per kind.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import and_
from sqlmodel import col
from sqlmodel import select

from app.models.consent_record import ConsentRecord
from app.repositories.base import BaseRepository


class ConsentRecordRepository(BaseRepository[ConsentRecord]):
    """Repository for ConsentRecord model operations."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize consent record repository.

        Args:
            session: Async database session
        """
        super().__init__(session, ConsentRecord)

    async def current_for(self, user_id: UUID) -> dict[str, ConsentRecord]:
        """The answer that stands right now, per kind.

        Newest row wins. Read in one query over the account's whole history
        rather than one query per kind, because the screen that shows these
        shows all of them at once and the history is a handful of rows.

        Args:
            user_id: The account to read.

        Returns:
            Kind to its newest answer. A kind never answered is absent, and
            what an absent answer means is the caller's rule.
        """
        query = self._apply_tenant_filter(
            select(ConsentRecord)
            .where(
                and_(
                    col(ConsentRecord.user_id) == user_id,
                    col(ConsentRecord.deleted_at).is_(None),
                )
            )
            .order_by(col(ConsentRecord.created_at))
        )
        result = await self.session.execute(query)
        # Oldest first, so a later row for the same kind replaces an
        # earlier one as the dictionary is built.
        return {row.kind: row for row in result.scalars().all()}

    async def history_for(
        self,
        user_id: UUID,
        kind: str | None = None,
    ) -> list[ConsentRecord]:
        """Every answer this account has given, oldest first.

        What a subject access or an audit actually asks for. Kept separate
        from :meth:`current_for` so a screen cannot accidentally render the
        whole history where it meant to render the current state.

        Args:
            user_id: The account to read.
            kind: Narrow to one question, or None for all of them.

        Returns:
            The answers in the order they were given.
        """
        conditions = [
            col(ConsentRecord.user_id) == user_id,
            col(ConsentRecord.deleted_at).is_(None),
        ]
        if kind is not None:
            conditions.append(col(ConsentRecord.kind) == kind)
        query = self._apply_tenant_filter(
            select(ConsentRecord)
            .where(and_(*conditions))
            .order_by(col(ConsentRecord.created_at))
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def record(
        self,
        user_id: UUID,
        kind: str,
        is_granted: bool,
        document_version: str | None = None,
    ) -> ConsentRecord:
        """Append one answer.

        Always an insert. Re-answering the same way writes another row, and
        that is correct: it is evidence the question was put again and
        answered again, which is exactly what a re-consent prompt is for.

        Args:
            user_id: The account answering.
            kind: What is being answered.
            is_granted: What they said.
            document_version: Which version of the text, where there is one.

        Returns:
            The stored answer.
        """
        return await self.create(
            {
                "user_id": user_id,
                "kind": kind,
                "is_granted": is_granted,
                "document_version": document_version,
            }
        )
