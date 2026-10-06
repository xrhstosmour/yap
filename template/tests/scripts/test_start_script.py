"""Tests the development server entry point.

The port the server listens on is written in more than one place, the script
that starts it and the documentation that tells you where to look. When those
drifted apart the links in the README pointed at nothing, and the smoke walk
of a downstream project went looking for a server that was serving somewhere
else.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
START = PROJECT_DIR / "scripts" / "start.sh"
README = PROJECT_DIR / "README.md"


def _default_port() -> str:
    """Read the port `start.sh` serves on when given no arguments.

    Returns:
        The port as it is written in the script.
    """
    match = re.search(r"^PORT=(\d+)$", START.read_text(), re.MULTILINE)
    assert match is not None, "start.sh has no default port"
    return match.group(1)


class TestStartScript:
    """What a reader is promised is what the script does."""

    def test_the_script_is_executable(self) -> None:
        """Copier does not preserve a mode the template never had."""
        assert START.exists()
        assert START.stat().st_mode & 0o111, f"{START} is not executable"

    def test_the_documented_port_is_the_port_it_serves(self) -> None:
        """The README links are the ones a new reader clicks first."""
        port = _default_port()
        text = README.read_text()
        for path in ("/try", "/documentation"):
            assert f"http://localhost:{port}{path}" in text, (
                f"README does not document {path} on port {port}"
            )

    def test_an_unknown_argument_is_refused(self) -> None:
        """Silently ignoring a typo starts a server nobody asked for."""
        result = subprocess.run(
            ["bash", str(START), "--nope"],
            capture_output=True,
            text=True,
            cwd=PROJECT_DIR,
        )
        assert result.returncode == 2
        assert "Unknown argument" in result.stderr

    def test_a_missing_environment_file_points_at_setup(self) -> None:
        """Running the wrong script first is the common mistake."""
        scratch = PROJECT_DIR / ".pytest_cache" / "start_without_env"
        shutil.rmtree(scratch, ignore_errors=True)
        (scratch / "scripts").mkdir(parents=True)
        shutil.copy(START, scratch / "scripts" / "start.sh")

        result = subprocess.run(
            ["bash", str(scratch / "scripts" / "start.sh")],
            capture_output=True,
            text=True,
            cwd=scratch,
        )
        shutil.rmtree(scratch, ignore_errors=True)

        assert result.returncode == 1
        assert "setup.sh" in result.stderr
