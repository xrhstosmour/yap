"""Tests that the lint and type-check excludes point at real directories.

`pyproject.toml` and `mypy-ci.toml` both excluded `alembic/versions`. The
migrations directory is `migrations/versions`, and has been since the
template renamed it, so the exclude matched nothing. Alembic writes its
revisions with `Union[str, None]` annotations and `from typing import
Sequence` imports, which `ruff` rejects under this configuration, so
`ruff check .` reported dozens of errors in generated files nobody edits.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]
MIGRATIONS = PROJECT / "migrations" / "versions"

CONFIGURATIONS = {
    "pyproject.toml [tool.mypy]": ("pyproject.toml", ("tool", "mypy")),
    "pyproject.toml [tool.ruff]": ("pyproject.toml", ("tool", "ruff")),
    "mypy-ci.toml [tool.mypy]": ("mypy-ci.toml", ("tool", "mypy")),
}


def _excludes(file_name: str, section: tuple[str, ...]) -> list[str]:
    """Read one configuration section's `exclude` list.

    Args:
        file_name: Configuration file at the project root.
        section: Table path within that file.

    Returns:
        The configured exclude patterns.
    """
    table = tomllib.loads((PROJECT / file_name).read_text())
    for key in section:
        table = table[key]
    excludes: list[str] = table["exclude"]
    return excludes


class TestExcludePaths:
    """An exclude that matches nothing is not an exclude."""

    @pytest.mark.parametrize("name", sorted(CONFIGURATIONS))
    def test_every_path_like_exclude_exists(self, name: str) -> None:
        """A stale path silently stops excluding without any warning.

        Args:
            name: Which configuration section to check.
        """
        missing = [
            pattern
            for pattern in _excludes(*CONFIGURATIONS[name])
            if "/" in pattern and not (PROJECT / pattern).is_dir()
        ]

        assert missing == [], f"{name} excludes paths that do not exist: {missing}"

    @pytest.mark.parametrize("name", sorted(CONFIGURATIONS))
    def test_generated_migrations_are_excluded(self, name: str) -> None:
        """Alembic writes them, nobody edits them, they are not ours to style.

        Args:
            name: Which configuration section to check.
        """
        assert "migrations/versions" in _excludes(*CONFIGURATIONS[name])


class TestLintIsClean:
    """The point of the exclude, checked end to end."""

    def test_ruff_accepts_the_project(self) -> None:
        """`ruff check .` has to be usable, not just `ruff check app/ tests/`."""
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "migrations/"],
            cwd=PROJECT,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stdout
