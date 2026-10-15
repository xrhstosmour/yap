"""Tests what the deploy script refuses before it touches a server.

Everything checked here fails on the operator's machine, so a deploy with
a placeholder secret or development settings never reaches production.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "scripts" / "deploy.sh"

COMPLETE = "ENVIRONMENT=production\nSECRET_KEY=generated-elsewhere\n"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project with the deploy script, assembled, and complete settings."""
    (tmp_path / "scripts").mkdir()
    shutil.copy2(DEPLOY, tmp_path / "scripts" / "deploy.sh")
    (tmp_path / "containers").mkdir()
    (tmp_path / ".env.production").write_text(COMPLETE)
    return tmp_path


def _deploy(project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the copied deploy script inside the project.

    Args:
        project: The project directory holding the script.
        *arguments: Passed to the script as they are.

    Returns:
        The finished process.
    """
    return subprocess.run(
        ["bash", str(project / "scripts" / "deploy.sh"), *arguments],
        capture_output=True,
        text=True,
        cwd=project,
        timeout=30,
    )


class TestDeployScript:
    """Mistakes stop the deploy on this machine."""

    def test_the_script_is_executable(self) -> None:
        """Copier does not preserve a mode the template never had."""
        assert DEPLOY.stat().st_mode & 0o111, f"{DEPLOY} is not executable"

    def test_a_missing_address_shows_the_usage(self, project: Path) -> None:
        """Both the server and the address are required."""
        result = _deploy(project, "deploy@server.example")
        assert result.returncode == 2
        assert "Usage" in result.stderr

    @pytest.mark.parametrize(
        "address", ["Example.com", "example", "example.com/api", "a b.com"]
    )
    def test_an_address_that_is_neither_is_refused(
        self, project: Path, address: str
    ) -> None:
        """A malformed domain would become a router rule and a certificate request."""
        result = _deploy(project, "deploy@server.example", address)
        assert result.returncode == 1
        assert "neither a lowercase domain nor an IP address" in result.stderr

    def test_placeholders_are_named_without_their_values(self, project: Path) -> None:
        """The error lists keys only, settings can hold real secrets."""
        (project / ".env.production").write_text(
            COMPLETE + "POSTGRESQL_PASSWORD=your-postgresql-password\n"
        )
        result = _deploy(project, "deploy@server.example", "app.example.com")
        assert result.returncode == 1
        assert "POSTGRESQL_PASSWORD" in result.stderr
        assert "your-postgresql-password" not in result.stderr

    def test_development_settings_are_refused(self, project: Path) -> None:
        """A production server running in local mode exposes debug behaviour."""
        (project / ".env.production").write_text("ENVIRONMENT=local\n")
        result = _deploy(project, "deploy@server.example", "app.example.com")
        assert result.returncode == 1
        assert "set it to production" in result.stderr

    def test_missing_settings_are_refused(self, project: Path) -> None:
        """The local `.env` is never shipped in place of production settings."""
        (project / ".env.production").unlink()
        result = _deploy(project, "deploy@server.example", "app.example.com")
        assert result.returncode == 1
        assert "No .env.production" in result.stderr
