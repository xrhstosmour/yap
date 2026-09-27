"""Unit tests for claiming a handle."""

from __future__ import annotations

from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import system_context
from app.repositories.user_repository import UserRepository
from app.schemas.user import UserCreate
from app.services.user_service import UserService
from app.services.username import UsernameTakenError
from app.services.username import resolve_username_change


async def _account(session: AsyncSession, email: str, handle: str | None = None):
    """Make an account, optionally already holding a handle."""
    service = UserService(cast(AsyncSession, session))
    user = await service.create(
        UserCreate(email=email, password="password123", full_name="Test")
    )
    if handle is not None:
        users = UserRepository(cast(AsyncSession, session))
        with system_context():
            await users.update(
                user.id, await resolve_username_change(users, user.id, handle)
            )
    return user


class TestResolveUsernameChange:
    """Tests for the one place that decides what a handle is."""

    @pytest.mark.asyncio
    async def test_returns_the_trimmed_handle_and_its_normal_form(
        self,
        session: AsyncSession,
    ) -> None:
        """Both columns come back, so a caller cannot write one alone."""
        user = await _account(session, "claimer@example.com")
        users = UserRepository(cast(AsyncSession, session))

        changes = await resolve_username_change(users, user.id, "  Anna  ")

        assert changes["username"] == "Anna"
        assert changes["username_normalized"] == "anna"

    @pytest.mark.asyncio
    async def test_refuses_a_handle_somebody_else_holds(
        self,
        session: AsyncSession,
    ) -> None:
        """The conflict is an error rather than an integrity failure."""
        await _account(session, "holder@example.com", "anna")
        other = await _account(session, "other@example.com")
        users = UserRepository(cast(AsyncSession, session))

        with pytest.raises(UsernameTakenError):
            await resolve_username_change(users, other.id, "anna")

    @pytest.mark.asyncio
    async def test_padding_does_not_make_a_new_handle(
        self,
        session: AsyncSession,
    ) -> None:
        """A padded handle collides with the bare one, and is stored bare.

        This holds today because the lookup normalizes before comparing
        and normalizing strips. Pinned here so that stays true: a
        normalizer that kept whitespace would otherwise hand out two
        handles that read as one name, and nothing else would notice.
        """
        await _account(session, "padded-holder@example.com", "anna")
        other = await _account(session, "padded-other@example.com")
        users = UserRepository(cast(AsyncSession, session))

        with pytest.raises(UsernameTakenError):
            await resolve_username_change(users, other.id, "  anna  ")

    @pytest.mark.asyncio
    async def test_keeping_your_own_handle_is_not_a_conflict(
        self,
        session: AsyncSession,
    ) -> None:
        """Otherwise saving a profile twice would fail the second time."""
        user = await _account(session, "keeper@example.com", "anna")
        users = UserRepository(cast(AsyncSession, session))

        changes = await resolve_username_change(users, user.id, "anna")

        assert changes["username"] == "anna"
