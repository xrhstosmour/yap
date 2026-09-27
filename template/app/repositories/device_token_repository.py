"""Device token repository for push notification delivery.

This module provides the DeviceTokenRepository class for registering
installs and resolving the tokens a notification should be sent to.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import and_
from sqlmodel import col
from sqlmodel import select

from app.core.logging import get_logger
from app.models.device_token import DevicePlatform
from app.models.device_token import DeviceToken
from app.repositories.base import BaseRepository

logger = get_logger("repository.device_token")


class DeviceTokenRepository(BaseRepository[DeviceToken]):
    """Repository for DeviceToken model operations."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize device token repository.

        Args:
            session: Async database session
        """
        super().__init__(session, DeviceToken)

    async def register(
        self,
        user_id: UUID,
        token: str,
        platform: DevicePlatform,
    ) -> DeviceToken:
        """Claim a token for a user, creating or reassigning the row.

        The client calls this on every launch, because the push service
        may hand the app a new token at any time and never says which of
        the two is current. So this has to be idempotent for the common
        case where nothing changed, and it has to move a token that now
        belongs to somebody else rather than refuse it: a shared phone
        signed into a second account would otherwise keep delivering the
        first account's notifications.

        Args:
            user_id: The account this install is signed into.
            token: The push service's address for this install.
            platform: Which store the install came from.

        Returns:
            The registered token row.
        """
        existing = await self.get_by_token(token)
        if existing is not None:
            existing.user_id = user_id
            existing.platform = platform
            existing.last_seen_at = datetime.now(UTC)
            # Undeletes a row pruned by an earlier send. The push service
            # handing this token back out means it is live again.
            existing.deleted_at = None
            self.session.add(existing)
            await self.session.flush()
            return existing

        return await self.create(
            {
                "user_id": user_id,
                "token": token,
                "platform": platform,
                "last_seen_at": datetime.now(UTC),
            }
        )

    async def get_by_token(self, token: str) -> DeviceToken | None:
        """Get a token row whether or not it is soft deleted.

        Deleted rows are included on purpose: `register` reuses one rather
        than inserting a duplicate that the unique constraint would
        refuse.

        Deliberately **not** tenant filtered, which the unique constraint
        on `token` forces. A token addresses one physical install, so two
        tenants cannot both legitimately hold it, and a tenant-scoped
        lookup would miss the row and then fail the insert against the
        constraint. A phone handed to somebody in another tenant is the
        case that makes this matter, and leaving it registered to its
        previous owner would keep delivering their notifications to a
        stranger.

        Nothing read here reaches a caller, so the cross-tenant read
        cannot leak: `register` reassigns the row, and `forget` checks the
        owner before touching it.

        Args:
            token: The push service's address for an install.

        Returns:
            The row, or None.
        """
        result = await self.session.execute(
            select(DeviceToken).where(col(DeviceToken.token) == token)
        )
        return result.scalars().first()

    async def for_users(self, user_ids: list[UUID]) -> list[DeviceToken]:
        """Every live token belonging to any of these accounts.

        One query for the whole audience rather than one per recipient,
        which is what fanning a single event out to a few hundred
        followers would otherwise cost.

        Args:
            user_ids: The accounts to collect tokens for.

        Returns:
            Their tokens, in no particular order. Empty if the list is.
        """
        if not user_ids:
            return []
        query = self._apply_tenant_filter(
            select(DeviceToken).where(
                and_(
                    col(DeviceToken.user_id).in_(user_ids),
                    col(DeviceToken.deleted_at).is_(None),
                )
            )
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def forget(self, token: str, user_id: UUID | None = None) -> bool:
        """Drop a token, on sign-out or when the push service rejects it.

        Soft deleted rather than removed, so the row is there to be
        reclaimed if the same token comes back, which is what happens when
        somebody signs out and straight back in.

        Args:
            token: The push service's address for an install.
            user_id: When given, only forget the token if this account
                holds it. A push token is not a secret, and without this
                anyone who came by one could silence somebody else's
                phone. `None` is for the sender, which has already
                learned from the push service that the token is dead and
                is not acting for any particular user.

        Returns:
            Whether a row was found to forget.
        """
        existing = await self.get_by_token(token)
        if existing is None or existing.deleted_at is not None:
            return False
        if user_id is not None and existing.user_id != user_id:
            return False
        existing.deleted_at = datetime.now(UTC)
        self.session.add(existing)
        await self.session.flush()
        return True
