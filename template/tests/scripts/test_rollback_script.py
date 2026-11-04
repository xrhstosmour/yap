"""Tests what the rollback script refuses before it touches a server."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
ROLLBACK = SCRIPTS / "rollback.sh"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project with the rollback script and what it sources."""
    (tmp_path / "scripts").mkdir()
    for name in ("rollback.sh", "remote.sh"):
        shutil.copy2(SCRIPTS / name, tmp_path / "scripts" / name)
    return tmp_path


def _rollback(project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the copied rollback script inside the project.

    Args:
        project: The project directory holding the script.
        *arguments: Passed to the script as they are.

    Returns:
        The finished process.
    """
    return subprocess.run(
        ["bash", str(project / "scripts" / "rollback.sh"), *arguments],
        capture_output=True,
        text=True,
        cwd=project,
        timeout=30,
    )


class TestRollbackScript:
    """Mistakes stop the rollback on this machine."""

    def test_the_script_is_executable(self) -> None:
        """Copier does not preserve a mode the template never had."""
        assert ROLLBACK.stat().st_mode & 0o111, f"{ROLLBACK} is not executable"

    def test_a_missing_server_shows_the_usage(self, project: Path) -> None:
        """The server is required."""
        result = _rollback(project)
        assert result.returncode == 2
        assert "Usage" in result.stderr

    def test_a_server_that_is_not_user_at_host_is_refused(self, project: Path) -> None:
        """The value ends up in an `ssh` command."""
        result = _rollback(project, "server.example")
        assert result.returncode == 1
        assert "is not user@host" in result.stderr

    def test_a_release_with_a_shell_character_is_refused(self, project: Path) -> None:
        """The value reaches the server inside a command line."""
        result = _rollback(project, "deploy@server.example", "abc;touch x")
        assert result.returncode == 1
        assert "is not a release" in result.stderr
