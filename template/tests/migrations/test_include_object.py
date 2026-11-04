"""Tests which objects autogenerate and `alembic check` are told to ignore.

The trigram and full-text indexes are created with raw SQL in migrations, so
no model declares them. `alembic check` runs in a generated project's CI, and
without this filter it reports each one as an index to drop. The full-text
index added to `users` was not covered, which failed that step for every
project generated from the template.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"

RAW_INDEX = re.compile(
    r"CREATE (?:UNIQUE )?INDEX (?:CONCURRENTLY )?(?:IF NOT EXISTS )?([a-z0-9_]+)"
)


def _include_object() -> Callable[..., bool]:
    """The filter from `env.py`, which only runs inside an Alembic command.

    Returns:
        The `_include_object` function, compiled on its own.
    """
    source = (MIGRATIONS / "env.py").read_text()
    function = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "_include_object"
    )
    namespace: dict[str, object] = {}
    exec(  # noqa: S102
        compile(ast.Module([function], []), "env.py", "exec"), namespace
    )
    include: Callable[..., bool] = namespace["_include_object"]  # type: ignore[assignment]
    return include


def _shown(type_: str, name: str | None) -> bool:
    """Whether autogenerate would look at an object."""
    return _include_object()(None, name, type_, True, None)


class TestIncludeObject:
    """Indexes only a migration knows about stay out of the comparison."""

    @pytest.mark.parametrize("name", ["ix_users_email_trgm", "ix_users_full_name_fts"])
    def test_raw_search_indexes_are_hidden(self, name: str) -> None:
        """Otherwise `alembic check` proposes dropping them."""
        assert not _shown("index", name)

    @pytest.mark.parametrize("name", ["ix_users_email_hash", "uq_users_username"])
    def test_indexes_a_model_declares_are_still_compared(self, name: str) -> None:
        """Hiding by suffix must not hide a drift in the rest."""
        assert _shown("index", name)

    def test_a_table_with_the_suffix_is_still_compared(self) -> None:
        """Only indexes are filtered."""
        assert _shown("table", "documents_fts")

    def test_an_unnamed_object_is_still_compared(self) -> None:
        """Reflection can hand over objects without a name."""
        assert _shown("index", None)

    def test_every_index_a_migration_creates_by_hand_is_hidden(self) -> None:
        """A new raw index with another suffix would fail the check again."""
        created = {
            name
            for path in (MIGRATIONS / "versions").glob("*.py")
            for name in RAW_INDEX.findall(path.read_text())
        }
        assert created, "no hand-written index found, the pattern is out of date"
        assert [name for name in sorted(created) if _shown("index", name)] == []
