"""Unit tests for UserRepository database operations."""

from __future__ import annotations

import pytest
from sqlalchemy import literal
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import system_context
from app.core.tenant import tenant_context
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user import UserRole
from app.repositories.user_repository import UserRepository


class TestUserRepository:
    """Tests for UserRepository operations."""

    @pytest.fixture
    def anyio_backend(self) -> str:
        """Provide asyncio backend for anyio-marked tests.

        Returns:
            The backend name.
        """
        return "asyncio"

    async def _create_tenant(
        self, session: AsyncSession, slug: str = "test-org"
    ) -> Tenant:
        """Create a test tenant for FK constraints.

        Args:
            session: Async database session fixture.
            slug: Unique slug for the tenant.

        Returns:
            Persisted Tenant instance.
        """
        tenant = Tenant(name="Test Org", slug=slug)
        session.add(tenant)
        await session.commit()
        await session.refresh(tenant)
        return tenant

    @pytest.mark.anyio
    async def test_create_user(self, session: AsyncSession) -> None:
        """create_user() should persist a user with all provided fields.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        tenant = await self._create_tenant(session)
        repo = UserRepository(session)

        user = await repo.create_user(
            email="alice@example.com",
            password_hash="hashed_pw_123",
            full_name="Alice Wonderland",
            tenant_id=tenant.id,
            role=UserRole.SUPERUSER,
            is_verified=True,
        )

        assert isinstance(user, User)
        assert user.id is not None
        assert user.email == "alice@example.com"
        assert user.hashed_password == "hashed_pw_123"
        assert user.full_name == "Alice Wonderland"
        assert user.tenant_id == tenant.id
        assert user.role == UserRole.SUPERUSER
        assert user.is_verified is True
        assert user.is_active is True
        assert user.token_version == 1

    @pytest.mark.anyio
    async def test_get_by_email_returns_user(self, session: AsyncSession) -> None:
        """get_by_email() should return the user with matching email.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = UserRepository(session)
        await repo.create_user(email="bob@example.com", password_hash="hash")

        found = await repo.get_by_email("bob@example.com")

        assert found is not None
        assert found.email == "bob@example.com"

    @pytest.mark.anyio
    async def test_get_by_email_returns_none_for_unknown(
        self, session: AsyncSession
    ) -> None:
        """get_by_email() should return None for a non-existent email.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = UserRepository(session)

        found = await repo.get_by_email("nobody@example.com")

        assert found is None

    @pytest.mark.anyio
    async def test_email_exists_returns_true(self, session: AsyncSession) -> None:
        """email_exists() should return True for a registered email.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = UserRepository(session)
        await repo.create_user(email="exists@example.com", password_hash="hash")

        assert await repo.email_exists("exists@example.com") is True

    @pytest.mark.anyio
    async def test_email_exists_returns_false_for_missing(
        self, session: AsyncSession
    ) -> None:
        """email_exists() should return False for an unregistered email.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = UserRepository(session)

        assert await repo.email_exists("missing@example.com") is False

    @pytest.mark.anyio
    async def test_email_exists_false_after_soft_delete(
        self, session: AsyncSession
    ) -> None:
        """A soft-deleted user's email must be free to re-register.

        Otherwise a self-deleted account (GDPR Article 17 erasure) blocks
        registration forever: email_exists() reports the address taken
        while get_by_email() reports no user, so the address is both
        unusable and unrecoverable.
        """
        repo = UserRepository(session)
        with system_context():
            user = await repo.create_user(
                email="deleted@example.com", password_hash="hash"
            )

            await repo.delete(user.id)

            exists = await repo.email_exists("deleted@example.com")

        assert exists is False

    @pytest.mark.anyio
    async def test_update_password(self, session: AsyncSession) -> None:
        """update_password() should update the hashed_password field.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = UserRepository(session)
        with system_context():
            user = await repo.create_user(
                email="charlie@example.com", password_hash="old_hash"
            )

            updated = await repo.update_password(user.id, "new_hash")

            assert updated is not None
            assert updated.hashed_password == "new_hash"

            # Verify persisted.
            found = await repo.get(user.id)

        assert found is not None
        assert found.hashed_password == "new_hash"

    @pytest.mark.anyio
    async def test_increment_token_version(self, session: AsyncSession) -> None:
        """increment_token_version() should increase token_version by 1.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = UserRepository(session)
        with system_context():
            user = await repo.create_user(
                email="token@example.com", password_hash="hash"
            )
            assert user.token_version == 1

            await repo.increment_token_version(user.id)

            # Refresh and check.
            found = await repo.get(user.id)
            assert found is not None
            assert found.token_version == 2

            # Increment again.
            await repo.increment_token_version(user.id)
            found = await repo.get(user.id)

        assert found is not None
        assert found.token_version == 3

    @pytest.mark.anyio
    async def test_increment_token_version_does_not_cross_tenants(
        self, session: AsyncSession
    ) -> None:
        """increment_token_version() must not invalidate another tenant's user.

        A bare `UPDATE users SET token_version = token_version + 1 WHERE id
        = :id` with no tenant filter would let a caller reached with an
        unvalidated ID invalidate every JWT of a user in another tenant.
        """
        owning_tenant = Tenant(name="Owning Org", slug="owning-org-tv")
        other_tenant = Tenant(name="Other Org", slug="other-org-tv")
        session.add(owning_tenant)
        session.add(other_tenant)
        await session.commit()
        await session.refresh(owning_tenant)
        await session.refresh(other_tenant)

        repo = UserRepository(session)
        with tenant_context(owning_tenant.id):
            user = await repo.create_user(
                email="owned-token@example.com",
                password_hash="hash",
                tenant_id=owning_tenant.id,
            )
        assert user.token_version == 1

        with tenant_context(other_tenant.id):
            await repo.increment_token_version(user.id)

        with tenant_context(owning_tenant.id):
            found = await repo.get(user.id)

        assert found is not None
        assert found.token_version == 1

    @pytest.mark.anyio
    async def test_search_by_name(
        self, monkeypatch: pytest.MonkeyPatch, session: AsyncSession
    ) -> None:
        """search() should return users matching the search query by name.

        `email` is encrypted at rest and intentionally excluded from
        `search()` (see `UserRepository.search`'s docstring): Fernet
        ciphertext is randomised per value and cannot support
        trigram/FTS matching, only the exact-match `email_hash` lookup
        used by `get_by_email()`. Only `full_name` is searched.

        Uses mocked FTS/trigram to avoid PostgreSQL-specific requirements
        in the SQLite test backend.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            session: Async database session fixture.

        Returns:
            None.
        """
        # Mock FTS/trigram condition builders to use simple LIKE patterns
        # that work in SQLite.
        from app.repositories.mixins import search as search_mod

        monkeypatch.setattr(
            search_mod,
            "build_fts_condition",
            lambda col, query_str, language=None: col.contains(query_str),
        )
        monkeypatch.setattr(
            search_mod,
            "build_trigram_condition",
            lambda col, query_str, threshold=None: col.contains(query_str),
        )
        # fts_rank_expr is only needed for ORDER BY in FTS mode; provide a dummy.
        monkeypatch.setattr(
            search_mod,
            "fts_rank_expr",
            lambda col, query_str, language=None: literal(0),
        )
        # Force FTS path by using a query long enough.
        monkeypatch.setattr(
            search_mod,
            "choose_mode",
            lambda query_str, min_fts_length=3: (search_mod.SearchMode.FTS, query_str),
        )

        repo = UserRepository(session)
        with system_context():
            await repo.create_user(
                email="alice@example.com",
                password_hash="hash",
                full_name="Alice Smith",
            )
            await repo.create_user(
                email="bob@example.com",
                password_hash="hash",
                full_name="Bob Jones",
            )
            await repo.create_user(
                email="carol@example.com",
                password_hash="hash",
                full_name="Carol Smith",
            )

            # Search for "Smith" by name.
            users, total = await repo.search("Smith")

            assert total == 2
            assert len(users) == 2
            names = {u.full_name for u in users}
            assert names == {"Alice Smith", "Carol Smith"}

            # Search for "Bob" by name.
            users, total = await repo.search("Bob")

            assert total == 1
            assert users[0].full_name == "Bob Jones"

            # Email is not searchable: a matching substring finds nothing.
            users, total = await repo.search("bob@example.com")

        assert total == 0
        assert users == []

    @pytest.mark.anyio
    async def test_get_by_email_excludes_soft_deleted(
        self, session: AsyncSession
    ) -> None:
        """get_by_email() should not return soft-deleted users.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repo = UserRepository(session)
        with system_context():
            user = await repo.create_user(
                email="gone@example.com", password_hash="hash"
            )
            await repo.delete(user.id, hard=False)

            found = await repo.get_by_email("gone@example.com")

        assert found is None
