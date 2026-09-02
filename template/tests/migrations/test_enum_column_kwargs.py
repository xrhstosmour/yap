"""Tests that migrations do not pass dialect-only kwargs to `sa.Enum`.

`20260718154026721` declared its column as
`sa.Enum("superuser", "user", name="userrole", create_type=False)`.
`create_type` belongs to `sqlalchemy.dialects.postgresql.ENUM`. The generic
`sa.Enum` swallows it: no attribute is set, no dialect option is recorded,
and no warning is raised. It read as a deliberate guard against a duplicate
`CREATE TYPE` while doing nothing at all, which is worse than absent,
because the next person to touch the migration trusts it.
"""

from __future__ import annotations

import ast
import io
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"

# Keywords `sa.Enum` accepts and then ignores, because they belong to a
# dialect-specific type rather than the generic one.
DIALECT_ONLY_KEYWORDS = {"create_type"}


def _enum_keywords(path: Path) -> set[str]:
    """Every keyword passed to an `Enum(...)` call in one migration.

    Args:
        path: The migration file to parse.

    Returns:
        The keyword names used across all `Enum` calls in that file.
    """
    keywords: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = function.attr if isinstance(function, ast.Attribute) else None
        if isinstance(function, ast.Name):
            name = function.id
        if name != "Enum":
            continue
        keywords.update(word.arg for word in node.keywords if word.arg)
    return keywords


class TestEnumKeywords:
    """A keyword that does nothing is a comment pretending to be code."""

    def test_no_migration_passes_a_dialect_only_keyword(self) -> None:
        """`sa.Enum` accepts them silently, so nothing else catches this."""
        offenders = {
            path.name: sorted(_enum_keywords(path) & DIALECT_ONLY_KEYWORDS)
            for path in sorted(VERSIONS.glob("*.py"))
            if _enum_keywords(path) & DIALECT_ONLY_KEYWORDS
        }

        assert offenders == {}


class TestEmittedDdl:
    """Why removing it is safe, stated as a test rather than a comment."""

    def test_adding_an_enum_column_never_emits_create_type(self) -> None:
        """So there was never a `CREATE TYPE` for the keyword to suppress."""
        buffer = io.StringIO()
        context = MigrationContext.configure(
            dialect_name="postgresql",
            opts={"as_sql": True, "output_buffer": buffer},
        )

        Operations(context).add_column(
            "users",
            sa.Column(
                "role",
                sa.Enum("superuser", "user", name="userrole"),
                nullable=True,
            ),
        )

        assert "CREATE TYPE" not in buffer.getvalue().upper()
