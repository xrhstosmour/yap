"""Tests that `synchronize.sh` finds inline conflict markers in markdown.

`copier update --conflict inline` writes markers into any file it merges,
`AGENTS.md` and `README.md` included. A scan that skipped markdown let
those through to a commit.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

SYNCHRONIZE = Path(__file__).resolve().parents[2] / "scripts" / "synchronize.sh"


def _scan(directory: Path) -> bool:
    """Run the script's own conflict scan, True when it reports a conflict.

    Exit 1 is grep finding nothing, any other failure is a broken scan.
    """
    match = re.search(r"if (grep -rq .+?); then", SYNCHRONIZE.read_text())
    assert match, "conflict scan not found in synchronize.sh"
    result = subprocess.run(["bash", "-c", match.group(1)], cwd=directory, check=False)
    assert result.returncode in (0, 1), "conflict scan failed to run"
    return result.returncode == 0


class TestConflictScan:
    """The scan has to cover every file type copier merges."""

    @pytest.mark.parametrize(
        "name", ["module.py", "AGENTS.md", "docker-compose.yml", "pyproject.toml"]
    )
    @pytest.mark.parametrize("marker", ["<<<<<<< before", ">>>>>>> after"])
    def test_marker_is_found(self, tmp_path: Path, name: str, marker: str) -> None:
        (tmp_path / name).write_text(f"line\n{marker}\nline\n")

        assert _scan(tmp_path)

    def test_markdown_rules_and_prose_are_not_conflicts(self, tmp_path: Path) -> None:
        """A setext underline of equals signs is not a marker."""
        (tmp_path / "README.md").write_text(
            "Title\n=======\n\nUse a << b and >> c in text.\n"
        )

        assert not _scan(tmp_path)
