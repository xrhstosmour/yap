"""Tests the rehash of `users.email_hash` to the lowercase address.

Rows written before `hash_for_email` hold the hash of the address as typed.
The migration moves them to the lowercase hash, except where another row
already holds it, because the unique index would refuse both.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import crypto
from app.models.tenant import Tenant
from app.repositories.user_repository import UserRepository

MIGRATION = next(
    (Path(__file__).resolve().parents[2] / "migrations" / "versions").glob(
        "*_hash_user_emails_without_letter_case.py"
    )
)


def _run_upgrade(connection: Connection) -> None:
    """Run the migration's `upgrade()` against `connection`."""
    specification = importlib.util.spec_from_file_location("rehash", MIGRATION)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    module.op = SimpleNamespace(get_bind=lambda: connection)
    module.upgrade()


async def _hash_of(session: AsyncSession, user_id: object) -> str:
    result = await session.execute(
        text("SELECT email_hash FROM users WHERE id = :id"), {"id": user_id}
    )
    return result.scalar_one()


class TestRehash:
    """What the migration does to rows that already exist."""

    @pytest.fixture
    def anyio_backend(self) -> str:
        return "asyncio"

    async def _user(self, session: AsyncSession, email: str, tenant_id: object):
        user = await UserRepository(session).create_user(
            email=email, password_hash="hash", tenant_id=tenant_id
        )
        # Back to the hash written before the case-insensitive one.
        await session.execute(
            text("UPDATE users SET email_hash = :hash WHERE id = :id"),
            {"hash": crypto.hash_for_search(email), "id": user.id},
        )
        return user

    @pytest.mark.anyio
    async def test_a_mixed_case_row_gets_the_lowercase_hash(
        self, session: AsyncSession
    ) -> None:
        tenant = Tenant(name="Rehash", slug="rehash-single")
        session.add(tenant)
        await session.flush()
        user = await self._user(session, "Solo@Example.com", tenant.id)

        await session.run_sync(lambda sync: _run_upgrade(sync.connection()))

        assert await _hash_of(session, user.id) == crypto.hash_for_email(
            "solo@example.com"
        )

    @pytest.mark.anyio
    async def test_of_two_mixed_case_rows_the_older_gets_the_lowercase_hash(
        self, session: AsyncSession
    ) -> None:
        tenant = Tenant(name="Rehash", slug="rehash-pair")
        session.add(tenant)
        await session.flush()
        older = await self._user(session, "Dana@Example.com", tenant.id)
        newer = await self._user(session, "DANA@Example.com", tenant.id)
        await session.execute(
            text(
                "UPDATE users SET created_at = created_at - interval '1 day' "
                "WHERE id = :id"
            ),
            {"id": older.id},
        )

        await session.run_sync(lambda sync: _run_upgrade(sync.connection()))

        assert await _hash_of(session, older.id) == crypto.hash_for_email(
            "dana@example.com"
        )
        # Left as it was, rather than failing the whole migration on the
        # unique index.
        assert await _hash_of(session, newer.id) == crypto.hash_for_search(
            "DANA@Example.com"
        )

    @pytest.mark.anyio
    async def test_a_lowercase_row_that_already_holds_the_hash_keeps_it(
        self, session: AsyncSession
    ) -> None:
        tenant = Tenant(name="Rehash", slug="rehash-held")
        session.add(tenant)
        await session.flush()
        mixed = await self._user(session, "Erin@Example.com", tenant.id)
        lowercase = await self._user(session, "erin@example.com", tenant.id)
        await session.execute(
            text(
                "UPDATE users SET created_at = created_at - interval '1 day' "
                "WHERE id = :id"
            ),
            {"id": mixed.id},
        )

        await session.run_sync(lambda sync: _run_upgrade(sync.connection()))

        held = crypto.hash_for_email("erin@example.com")
        assert await _hash_of(session, lowercase.id) == held
        assert await _hash_of(session, mixed.id) == crypto.hash_for_search(
            "Erin@Example.com"
        )
