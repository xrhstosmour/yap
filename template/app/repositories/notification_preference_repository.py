"""Notification preference repository.

This module provides the NotificationPreferenceRepository class for
reading and writing which kinds of push an account has turned off.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import and_
from sqlmodel import col
from sqlmodel import select

from app.models.notification_preference import NotificationPreference
from app.repositories.base import BaseRepository


class NotificationPreferenceRepository(BaseRepository[NotificationPreference]):
    """Repository for NotificationPreference model operations."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize notification preference repository.

        Args:
            session: Async database session
        """
        super().__init__(session, NotificationPreference)

    async def for_user(self, user_id: UUID) -> dict[str, bool]:
        """Every answer this account has given, by kind.

        Args:
            user_id: The account to read.

        Returns:
            Kind to whether it is enabled. Kinds never answered are
            absent, and the caller reads an absent kind as enabled.
        """
        query = self._apply_tenant_filter(
            select(NotificationPreference).where(
                and_(
                    col(NotificationPreference.user_id) == user_id,
                    col(NotificationPreference.deleted_at).is_(None),
                )
            )
        )
        result = await self.session.execute(query)
        return {row.kind: row.is_enabled for row in result.scalars().all()}

    async def record(
        self,
        user_id: UUID,
        kind: str,
        is_enabled: bool,
    ) -> NotificationPreference:
        """Record this account's answer for one kind.

        Named `record` rather than `set`, which would shadow the builtin
        inside the class body and make the `set[UUID]` on `muted` below
        resolve to this method.

        Updates the existing answer rather than adding a second one, so
        the unique constraint is never the thing that reports a repeated
        tap.

        Args:
            user_id: The account answering.
            kind: What the answer is about.
            is_enabled: Whether to deliver it.

        Returns:
            The stored answer.
        """
        query = self._apply_tenant_filter(
            select(NotificationPreference).where(
                and_(
                    col(NotificationPreference.user_id) == user_id,
                    col(NotificationPreference.kind) == kind,
                    col(NotificationPreference.deleted_at).is_(None),
                )
            )
        )
        existing: NotificationPreference | None = (
            (await self.session.execute(query)).scalars().first()
        )
        if existing is not None:
            existing.is_enabled = is_enabled
            self.session.add(existing)
            await self.session.flush()
            return existing

        return await self.create(
            {"user_id": user_id, "kind": kind, "is_enabled": is_enabled}
        )

    async def muted(self, kind: str, user_ids: list[UUID]) -> set[UUID]:
        """Which of these accounts have turned this kind off.

        One query for the whole audience, because this runs on the
        delivery path where the alternative is a lookup per recipient.

        Args:
            kind: What is about to be sent.
            user_ids: The accounts it would go to.

        Returns:
            The subset that has turned it off. Empty if the list is.
        """
        if not user_ids:
            return set()
        query = self._apply_tenant_filter(
            select(NotificationPreference).where(
                and_(
                    col(NotificationPreference.user_id).in_(user_ids),
                    col(NotificationPreference.kind) == kind,
                    col(NotificationPreference.is_enabled).is_(False),
                    col(NotificationPreference.deleted_at).is_(None),
                )
            )
        )
        result = await self.session.execute(query)
        return {row.user_id for row in result.scalars().all()}
