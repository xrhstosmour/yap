"""Tests the shape of the `files` dedup constraints at head.

The chain used to enforce two constraints on the way to head that are
stricter than the one it ends on: a global unique index on `content_hash`,
then a unique constraint on `(tenant_id, content_hash)`. Both are dropped
before head, so neither is part of the schema, but a database holding data
that head accepts still aborted on them and was left stranded partway
through the chain with no way forward.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"

# The two revisions whose constraints are superseded before head.
SUPERSEDED = (
    "20260731171240225_add_unique_constraint_on_files_content_hash.py",
    "20260812103128195_per_tenant_file_dedup.py",
)

FINAL_CONSTRAINT = "uq_files_tenant_id_uploaded_by_content_hash"


class TestHeadSchema:
    """What the migrated database actually carries."""

    @pytest.mark.anyio
    async def test_only_the_final_constraint_is_present(
        self, session: AsyncSession
    ) -> None:
        """Head dedups on the triple, and on nothing narrower.

        Args:
            session: Async database session fixture.
        """
        result = await session.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'files'::regclass AND contype = 'u'"
            )
        )
        constraints = {row[0] for row in result}

        assert FINAL_CONSTRAINT in constraints
        assert "uq_files_tenant_id_content_hash" not in constraints

    @pytest.mark.anyio
    async def test_the_global_index_is_gone(self, session: AsyncSession) -> None:
        """A global unique index would reject two tenants holding one file.

        Args:
            session: Async database session fixture.
        """
        result = await session.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'files'")
        )
        indexes = {row[0] for row in result}

        assert "ix_files_content_hash" not in indexes

    @pytest.mark.anyio
    async def test_two_tenants_may_hold_the_same_content(
        self, session: AsyncSession
    ) -> None:
        """The case the chain used to abort on has to be legal at head.

        Args:
            session: Async database session fixture.
        """
        result = await session.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = :name"
            ),
            {"name": FINAL_CONSTRAINT},
        )
        definition = result.scalar_one()

        # `tenant_id` leads the constraint, so differing tenants never collide.
        assert "tenant_id" in definition
        assert "uploaded_by" in definition
        assert "content_hash" in definition


class TestSupersededRevisionsEnforceNothing:
    """Guard the migrations themselves, not just the end state.

    The end-state tests above pass whether or not the intermediate steps
    enforce anything, because the constraints are dropped again before head.
    Only a database migrating through the chain feels the difference.
    """

    @pytest.mark.parametrize("filename", SUPERSEDED)
    def test_no_unique_constraint_is_created(self, filename: str) -> None:
        """Neither revision may add a constraint head does not keep.

        Args:
            filename: A migration whose constraint is superseded before head.
        """
        upgrade = next(
            node
            for node in ast.walk(ast.parse((VERSIONS / filename).read_text()))
            if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
        )

        for node in ast.walk(upgrade):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if not isinstance(function, ast.Attribute):
                continue

            assert function.attr != "create_unique_constraint", (
                f"{filename} adds a unique constraint that head does not keep, "
                "so the chain aborts on data head accepts"
            )
            if function.attr == "create_index":
                unique = [kw for kw in node.keywords if kw.arg == "unique"]
                assert not any(
                    isinstance(kw.value, ast.Constant) and kw.value.value is True
                    for kw in unique
                ), f"{filename} creates a unique index head does not keep"
