"""Tests that fetching one record does not drag in unbounded child tables."""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import tenant_context
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.user_repository import UserRepository

# The user row itself, plus its tenant. Both are bounded: exactly one row
# each, per fetched user.
EXPECTED_STATEMENTS = 2


class TestEagerLoadFanOut:
    """One `User` fetch runs on every authenticated request.

    Every collection on `User` and `Tenant` used to be `lazy="selectin"`,
    so that one fetch also pulled the user's API keys, the user's OAuth
    accounts, and every API key belonging to the whole tenant. The last of
    those grows with the tenant, so the cost of authenticating one user
    scaled with how many keys every other user in the organization held.
    """

    @pytest.fixture
    def anyio_backend(self) -> str:
        """Provide the asyncio backend for anyio-marked tests.

        Returns:
            The backend name.
        """
        return "asyncio"

    @pytest.mark.anyio
    async def test_fetching_a_user_stays_bounded(self, session: AsyncSession) -> None:
        """Fetching one user must not scale with the tenant's size.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = Tenant(name="Fan Out Org", slug="fan-out-org")
        session.add(tenant)
        await session.commit()
        await session.refresh(tenant)

        members = [
            User(
                email=f"member{index}@example.com",
                hashed_password="hash",
                tenant_id=tenant.id,
            )
            for index in range(5)
        ]
        session.add_all(members)
        await session.commit()
        for member in members:
            await session.refresh(member)

        # Drop the identity map, or the fetch below is served from memory.
        session.expunge_all()

        statements: list[str] = []

        @event.listens_for(session.sync_session, "do_orm_execute")
        def _record(state: object) -> None:
            statements.append(str(getattr(state, "statement", "")))

        with tenant_context(tenant.id):
            repository = UserRepository(session)
            found = await repository.get(members[0].id)

        assert found is not None
        assert len(statements) == EXPECTED_STATEMENTS, statements
        assert not any("api_keys" in statement for statement in statements)
        assert not any("oauth_accounts" in statement for statement in statements)
