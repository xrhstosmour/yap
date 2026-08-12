"""Unit tests for repository SearchMixin behavior."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import literal

from app.core.tenant import system_context
from app.core.tenant import tenant_context
from app.models.user import User
from app.repositories.base import BaseRepository
from app.repositories.mixins.search import SearchMixin


class RepositoryUnderTest(SearchMixin, BaseRepository[User]):
    """Test repository that composes SearchMixin with BaseRepository.

    Args:
        session: Async database session fixture.
    """

    def __init__(self, session) -> None:
        """Initialize repository with User model.

        Args:
            session: Async database session.
        """
        super().__init__(session, User)


class TestSearchMixin:
    """Tests for SearchMixin delegation and query behavior."""

    @pytest.fixture
    def anyio_backend(self) -> str:
        """Provide asyncio backend for anyio-marked tests.

        Returns:
            The backend name.
        """
        return "asyncio"

    @pytest.mark.anyio
    async def test_search_combined_delegates_to_fts_for_long_queries(
        self, monkeypatch: pytest.MonkeyPatch, session
    ) -> None:
        """Ensure long queries dispatch to search_fts().

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            session: Async database session fixture.

        Returns:
            None.
        """
        repository = RepositoryUnderTest(session)

        async def _mock_search_fts(**_kwargs: Any):  # noqa: ANN401
            return ["fts"], 1

        async def _mock_search_trigram(**_kwargs: Any):  # noqa: ANN401
            return ["trigram"], 1

        monkeypatch.setattr(repository, "search_fts", _mock_search_fts)
        monkeypatch.setattr(repository, "search_trigram", _mock_search_trigram)

        records, total = await repository.search_combined(
            query_str="alice",
            fields=["email"],
        )

        assert records == ["fts"]
        assert total == 1

    @pytest.mark.anyio
    async def test_search_combined_delegates_to_trigram_for_short_queries(
        self, monkeypatch: pytest.MonkeyPatch, session
    ) -> None:
        """Ensure short queries dispatch to search_trigram().

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            session: Async database session fixture.

        Returns:
            None.
        """
        repository = RepositoryUnderTest(session)

        async def _mock_search_fts(**_kwargs: Any):  # noqa: ANN401
            return ["fts"], 1

        async def _mock_search_trigram(**_kwargs: Any):  # noqa: ANN401
            return ["trigram"], 1

        monkeypatch.setattr(repository, "search_fts", _mock_search_fts)
        monkeypatch.setattr(repository, "search_trigram", _mock_search_trigram)

        records, total = await repository.search_combined(
            query_str="al",
            fields=["email"],
        )

        assert records == ["trigram"]
        assert total == 1

    @pytest.mark.anyio
    async def test_search_fts_applies_tenant_filter(
        self, monkeypatch: pytest.MonkeyPatch, session
    ) -> None:
        """Ensure search_fts() respects current tenant context.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            session: Async database session fixture.

        Returns:
            None.
        """
        monkeypatch.setattr(
            "app.repositories.mixins.search.build_fts_condition",
            lambda *_args, **_kwargs: literal(True),
        )
        monkeypatch.setattr(
            "app.repositories.mixins.search.fts_rank_expr",
            lambda *_args, **_kwargs: literal(0),
        )

        tenant_a = UUID("00000000-0000-0000-0000-00000000000a")
        tenant_b = UUID("00000000-0000-0000-0000-00000000000b")

        from app.models.tenant import Tenant

        session.add(Tenant(id=tenant_a, name="Tenant A", slug="tenant-a"))
        session.add(Tenant(id=tenant_b, name="Tenant B", slug="tenant-b"))
        await session.flush()

        user_a = User(
            email="tenant-a@example.com",
            hashed_password="hash",
            full_name="Tenant A",
            tenant_id=tenant_a,
        )
        user_b = User(
            email="tenant-b@example.com",
            hashed_password="hash",
            full_name="Tenant B",
            tenant_id=tenant_b,
        )
        session.add(user_a)
        session.add(user_b)
        await session.commit()

        repository = RepositoryUnderTest(session)

        with tenant_context(tenant_a):
            records, total = await repository.search_fts(
                query_str="tenant",
                fields=["email", "full_name"],
            )

        assert total == 1
        assert len(records) == 1
        assert records[0].email == "tenant-a@example.com"

    @pytest.mark.anyio
    async def test_search_fts_applies_soft_delete_filter(
        self, monkeypatch: pytest.MonkeyPatch, session
    ) -> None:
        """Ensure soft-deleted rows are excluded by default.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            session: Async database session fixture.

        Returns:
            None.
        """
        monkeypatch.setattr(
            "app.repositories.mixins.search.build_fts_condition",
            lambda *_args, **_kwargs: literal(True),
        )
        monkeypatch.setattr(
            "app.repositories.mixins.search.fts_rank_expr",
            lambda *_args, **_kwargs: literal(0),
        )

        active_user = User(
            email="active@example.com",
            hashed_password="hash",
            full_name="Active User",
        )
        deleted_user = User(
            email="deleted@example.com",
            hashed_password="hash",
            full_name="Deleted User",
            deleted_at=datetime.now(UTC),
        )
        session.add(active_user)
        session.add(deleted_user)
        await session.commit()

        repository = RepositoryUnderTest(session)
        with system_context():
            records, total = await repository.search_fts(
                query_str="user",
                fields=["email", "full_name"],
            )

        assert total == 1
        assert len(records) == 1
        assert records[0].email == "active@example.com"

    @pytest.mark.anyio
    async def test_search_fts_paginates(
        self, monkeypatch: pytest.MonkeyPatch, session
    ) -> None:
        """Ensure search_fts() applies skip and limit pagination.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            session: Async database session fixture.

        Returns:
            None.
        """
        monkeypatch.setattr(
            "app.repositories.mixins.search.build_fts_condition",
            lambda *_args, **_kwargs: literal(True),
        )
        monkeypatch.setattr(
            "app.repositories.mixins.search.fts_rank_expr",
            lambda *_args, **_kwargs: literal(0),
        )

        users = [
            User(
                email=f"user-{index}@example.com",
                hashed_password="hash",
                full_name=f"User {index}",
            )
            for index in range(5)
        ]
        session.add_all(users)
        await session.commit()

        repository = RepositoryUnderTest(session)
        with system_context():
            records, total = await repository.search_fts(
                query_str="user",
                fields=["email", "full_name"],
                skip=1,
                limit=2,
            )

        assert total == 5
        assert len(records) == 2

    @pytest.mark.anyio
    async def test_search_fts_raises_valueerror_for_empty_fields(self, session) -> None:
        """Ensure search_fts() raises ValueError when fields list is empty.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repository = RepositoryUnderTest(session)

        with pytest.raises(ValueError, match="at least one model attribute"):
            await repository.search_fts(
                query_str="anything",
                fields=[],
            )

    @pytest.mark.anyio
    async def test_search_fts_raises_valueerror_for_no_valid_fields(
        self, session
    ) -> None:
        """Ensure search_fts() raises ValueError when no field exists on model.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repository = RepositoryUnderTest(session)

        with pytest.raises(ValueError, match="No valid searchable fields"):
            await repository.search_fts(
                query_str="anything",
                fields=["nonexistent_field"],
            )

    @pytest.mark.anyio
    async def test_search_trigram_raises_valueerror_for_empty_fields(
        self, session
    ) -> None:
        """Ensure search_trigram() raises ValueError when fields list is empty.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repository = RepositoryUnderTest(session)

        with pytest.raises(ValueError, match="at least one model attribute"):
            await repository.search_trigram(
                query_str="anything",
                fields=[],
            )

    @pytest.mark.anyio
    async def test_search_trigram_raises_valueerror_for_no_valid_fields(
        self, session
    ) -> None:
        """Ensure search_trigram() raises ValueError when no field exists on model.

        Args:
            session: Async database session fixture.

        Returns:
            None.
        """
        repository = RepositoryUnderTest(session)

        with pytest.raises(ValueError, match="No valid searchable fields"):
            await repository.search_trigram(
                query_str="anything",
                fields=["nonexistent_field"],
            )
