"""Tests that no deprecated HTTP status constant is referenced.

Starlette renamed four of them to match the current RFC wording and kept the
old names as deprecated aliases. The aliases still work, so nothing breaks,
which is exactly why they linger: the only signal is a warning buried in test
output. This finds them by asking Starlette which names are deprecated rather
than by holding a list that would itself go stale.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest
from starlette import status

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "app"


def _deprecated_names() -> list[str]:
    """Ask Starlette which status constants it has deprecated.

    Reading this from the library rather than from a hardcoded list means the
    test keeps working when the next rename lands.

    Returns:
        The deprecated constant names, sorted.
    """
    names = []
    for name in dir(status):
        if not name.startswith("HTTP_"):
            continue
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            getattr(status, name)
            if caught:
                names.append(name)
    return sorted(names)


def _references(name: str) -> list[str]:
    """Every source line referencing a constant.

    Args:
        name: The constant to look for.

    Returns:
        The offending lines, prefixed with their file and line number.
    """
    found = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if name in line:
                relative = path.relative_to(SOURCE_ROOT.parent)
                found.append(f"{relative}:{number}: {line.strip()}")
    return found


class TestStatusConstants:
    """A deprecated alias works until the release that removes it."""

    def test_starlette_still_marks_some_as_deprecated(self) -> None:
        """If this breaks, the library changed and the guard below is moot."""
        assert _deprecated_names(), (
            "Starlette reports no deprecated status constants, so this guard "
            "no longer guards anything and should be removed"
        )

    @pytest.mark.parametrize("name", _deprecated_names())
    def test_the_deprecated_constant_is_not_used(self, name: str) -> None:
        """The replacement is the same integer, so this is free to adopt."""
        references = _references(name)
        assert not references, "\n".join([f"{name} is deprecated:", *references])
