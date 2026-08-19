"""Tests for OAuthAccountRepository database operations."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.oauth_account import (
    OAuthAccount,  # noqa: F401, registers table for create_all
)
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.oauth_account_repository import OAuthAccountRepository


class TestOAuthAccountRepository:
    """Tests for OAuthAccountRepository operations."""

    @pytest.fixture
    def anyio_backend(self) -> str:
        return "asyncio"

    async def _create_tenant(
        self, session: AsyncSession, slug: str = "test-org"
    ) -> Tenant:
        """Create a test tenant for FK constraints."""
        tenant = Tenant(name="Test Org", slug=slug)
        session.add(tenant)
        await session.commit()
        await session.refresh(tenant)
        return tenant

    async def _create_user(
        self, session: AsyncSession, email: str = "test@example.com"
    ) -> User:
        """Create a test user for FK constraints."""
        user = User(email=email, hashed_password="hash")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user

    @pytest.mark.anyio
    async def test_create_link_creates_oauth_account_with_all_fields(
        self, session: AsyncSession
    ) -> None:
        """create_link() should persist an OAuthAccount with all provided fields."""
        tenant = await self._create_tenant(session)
        user = await self._create_user(session)

        repo = OAuthAccountRepository(session)
        account = await repo.create_link(
            user_id=user.id,
            provider="google",
            provider_user_id="google-user-123",
            provider_email="user@gmail.com",
            tenant_id=tenant.id,
        )

        assert account.id is not None
        assert account.user_id == user.id
        assert account.provider == "google"
        assert account.provider_user_id == "google-user-123"
        assert account.provider_email == "user@gmail.com"
        assert account.tenant_id == tenant.id

    @pytest.mark.anyio
    async def test_get_by_provider_returns_account_for_valid_combo(
        self, session: AsyncSession
    ) -> None:
        """get_by_provider() should return the OAuthAccount for a matching provider + user_id."""
        user = await self._create_user(session)
        repo = OAuthAccountRepository(session)

        await repo.create_link(
            user_id=user.id,
            provider="google",
            provider_user_id="sub-abc",
        )

        found = await repo.get_by_provider("google", "sub-abc")
        assert found is not None
        assert found.provider == "google"
        assert found.provider_user_id == "sub-abc"
        assert found.user_id == user.id

    @pytest.mark.anyio
    async def test_get_by_provider_returns_none_for_wrong_combo(
        self, session: AsyncSession
    ) -> None:
        """get_by_provider() should return None when no matching row exists."""
        user = await self._create_user(session)
        repo = OAuthAccountRepository(session)

        await repo.create_link(
            user_id=user.id,
            provider="google",
            provider_user_id="real-sub",
        )

        found = await repo.get_by_provider("google", "nonexistent")
        assert found is None

        found = await repo.get_by_provider("apple", "real-sub")
        assert found is None

    @pytest.mark.anyio
    async def test_get_user_accounts_returns_linked_accounts(
        self, session: AsyncSession
    ) -> None:
        """get_user_accounts() should return all OAuth accounts belonging to a user."""
        user = await self._create_user(session)
        repo = OAuthAccountRepository(session)

        await repo.create_link(
            user_id=user.id, provider="google", provider_user_id="g-1"
        )
        await repo.create_link(
            user_id=user.id, provider="apple", provider_user_id="a-1"
        )

        accounts = await repo.get_user_accounts(user.id)
        assert len(accounts) == 2
        providers = {a.provider for a in accounts}
        assert providers == {"google", "apple"}

    @pytest.mark.anyio
    async def test_get_user_accounts_returns_empty_list_for_user_with_no_accounts(
        self, session: AsyncSession
    ) -> None:
        """get_user_accounts() should return an empty list when the user has no linked accounts."""
        user = await self._create_user(session)
        repo = OAuthAccountRepository(session)

        accounts = await repo.get_user_accounts(user.id)
        assert accounts == []
