"""Tests what the deploy script refuses before it touches a server.

Everything checked here fails on the operator's machine, so a deploy with
a placeholder secret or development settings never reaches production.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
DEPLOY = SCRIPTS / "deploy.sh"

COMPLETE = "ENVIRONMENT=production\nSECRET_KEY=generated-elsewhere\n"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project with the deploy script, assembled, and complete settings."""
    (tmp_path / "scripts").mkdir()
    shutil.copy2(DEPLOY, tmp_path / "scripts" / "deploy.sh")
    shutil.copy2(SCRIPTS / "remote.sh", tmp_path / "scripts" / "remote.sh")
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

    @pytest.mark.skipif(shutil.which("rsync") is None, reason="needs rsync")
    def test_the_servers_certificates_survive_a_deploy_without_them(
        self, tmp_path: Path
    ) -> None:
        """A checkout from the GitHub workflow has no git ignored certificates.

        `--delete` used to remove the server's, and the broker's bind mounts
        became directories the next time it was recreated. The script's own
        filters are run here against a server that has them.
        """
        filters = [
            f"--{kind}={rule}"
            for kind, rule in re.findall(
                r"--(exclude|filter)='([^']+)'", DEPLOY.read_text()
            )
        ]
        source, server = tmp_path / "source", tmp_path / "server"
        (source / "config").mkdir(parents=True)
        (server / "config").mkdir(parents=True)
        (source / "app.py").write_text("")
        for name in ("server.pem", "server.key", "server.crt", "ca_key.pem"):
            (server / "config" / name).write_text("kept")
        (server / "stale.txt").write_text("")

        subprocess.run(
            ["rsync", "-a", "--delete", *filters, f"{source}/", f"{server}/"],
            check=True,
            timeout=30,
        )

        assert (server / "config" / "server.pem").exists()
        assert (server / "config" / "server.key").exists()
        assert (server / "config" / "server.crt").exists()
        # The CA key is deleted on purpose, nothing at runtime reads it.
        assert not (server / "config" / "ca_key.pem").exists()
        assert not (server / "stale.txt").exists()

    def test_missing_settings_are_refused(self, project: Path) -> None:
        """The local `.env` is never shipped in place of production settings."""
        (project / ".env.production").unlink()
        result = _deploy(project, "deploy@server.example", "app.example.com")
        assert result.returncode == 1
        assert "No .env.production" in result.stderr
