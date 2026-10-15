"""Tests that the static website survives a template update.

`synchronize.sh` reads which optional services a project has from the
includes in `docker-compose.yml`. Were the website's include not detected,
the next update would answer `include_website` with false and remove it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INCLUDE = "containers/web/static/nginx/docker-compose.yml"


@pytest.fixture
def compose() -> str:
    """Skip when the project was generated without the website."""
    content = (ROOT / "docker-compose.yml").read_text()
    if INCLUDE not in content:
        pytest.skip("No static website in this project.")
    return content


class TestWebsite:
    """The website include, its files, and its detection agree."""

    def test_the_site_has_a_page(self, compose: str) -> None:
        """The server answers 404 for everything without one."""
        assert (ROOT / "website" / "index.html").is_file()

    def test_synchronize_detects_it(self, compose: str) -> None:
        """An update keeps the website rather than removing it."""
        script = (ROOT / "scripts" / "synchronize.sh").read_text()
        function = next(
            line for line in script.splitlines() if line.startswith("has_extra()")
        )
        result = subprocess.run(
            ["bash", "-c", f'{function}\nhas_extra "nginx"'],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "true"
