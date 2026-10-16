"""Tests that the map tile cache survives a template update.

`synchronize.sh` reads which optional services a project has from the
includes in `docker-compose.yml`. Were the tile cache's include not
detected, the next update would answer `include_tiles` with false and
remove it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INCLUDE = "containers/web/maps/tiles/docker-compose.yml"


class TestTiles:
    """The tile cache include and its detection agree."""

    def test_synchronize_detects_it(self) -> None:
        """An update keeps the tile cache rather than removing it."""
        if INCLUDE not in (ROOT / "docker-compose.yml").read_text():
            pytest.skip("No tile cache in this project.")
        script = (ROOT / "scripts" / "synchronize.sh").read_text()
        function = next(
            line for line in script.splitlines() if line.startswith("has_extra()")
        )
        result = subprocess.run(
            ["bash", "-c", f'{function}\nhas_extra "tiles"'],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "true"
