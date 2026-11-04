"""Tests what the logs script refuses and what it asks the server for."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
LOGS = SCRIPTS / "logs.sh"

# An `ssh` that records the command it was given instead of connecting.
SSH_STUB = '#!/usr/bin/env bash\nprintf \'%s\\n\' "${@: -1}" > "$FAKE_SSH_COMMAND"\n'


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project with the logs script, what it sources, and a stub `ssh`."""
    (tmp_path / "scripts").mkdir()
    for name in ("logs.sh", "remote.sh"):
        shutil.copy2(SCRIPTS / name, tmp_path / "scripts" / name)
    (tmp_path / "bin").mkdir()
    ssh = tmp_path / "bin" / "ssh"
    ssh.write_text(SSH_STUB)
    ssh.chmod(0o755)
    return tmp_path


def _logs(project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the copied logs script inside the project.

    Args:
        project: The project directory holding the script.
        *arguments: Passed to the script as they are.

    Returns:
        The finished process.
    """
    environment = {
        "PATH": f"{project / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "FAKE_SSH_COMMAND": str(project / "command"),
    }
    return subprocess.run(
        ["bash", str(project / "scripts" / "logs.sh"), *arguments],
        capture_output=True,
        text=True,
        cwd=project,
        timeout=30,
        env=environment,
    )


class TestLogsScript:
    """Mistakes stop on this machine, a valid call asks for the right logs."""

    def test_the_script_is_executable(self) -> None:
        """Copier does not preserve a mode the template never had."""
        assert LOGS.stat().st_mode & 0o111, f"{LOGS} is not executable"

    def test_a_missing_server_shows_the_usage(self, project: Path) -> None:
        """The server is required."""
        result = _logs(project)
        assert result.returncode == 2
        assert "Usage" in result.stderr

    @pytest.mark.parametrize("service", ["app; touch x", "$(id)", "App"])
    def test_a_service_that_is_not_a_name_is_refused(
        self, project: Path, service: str
    ) -> None:
        """The value ends up in a command line on the server."""
        result = _logs(project, "deploy@server.example", service)
        assert result.returncode == 1
        assert "is not a service name" in result.stderr

    def test_a_line_count_that_is_not_a_number_is_refused(self, project: Path) -> None:
        """The value ends up in a command line on the server."""
        result = _logs(project, "deploy@server.example", "-n", "5; id")
        assert result.returncode == 1
        assert "is not a number of lines" in result.stderr

    def test_the_server_is_asked_for_one_service_and_follows(
        self, project: Path
    ) -> None:
        """Options reach the compose command that runs on the server."""
        result = _logs(project, "deploy@server.example", "app", "-f", "-n", "20")
        assert result.returncode == 0, result.stderr
        command = (project / "command").read_text()
        assert "logs --no-color --timestamps --tail 20 --follow app" in command
