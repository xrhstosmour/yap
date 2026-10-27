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


def _run_in_scratch(
    name: str,
    environment_file: str,
    *,
    certificate: bool = True,
    health: str = "healthy",
    handshake: int = 0,
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run a copy of `start.sh` with `docker`, `uv` and `sleep` stubbed.

    Args:
        name: Scratch directory name under `.pytest_cache`.
        environment_file: Contents of the scratch `.env`.
        certificate: Whether the RabbitMQ CA certificate exists.
        health: What `docker compose ps` reports for RabbitMQ.
        handshake: Exit status of the stubbed `openssl s_client` handshake.

    Returns:
        The finished process and the stubbed commands it called.
    """
    scratch = PROJECT_DIR / ".pytest_cache" / name
    shutil.rmtree(scratch, ignore_errors=True)
    (scratch / "scripts").mkdir(parents=True)
    (scratch / "bin").mkdir()
    shutil.copy(START, scratch / "scripts" / "start.sh")
    (scratch / ".env").write_text(environment_file)
    if certificate:
        certificates = scratch / "containers/distribute/brokers/rabbitmq/certificates"
        certificates.mkdir(parents=True)
        (certificates / "ca_certificate.pem").write_text("certificate")
    calls = scratch / "calls"
    stubs = {
        "docker": f'echo "docker $*" >> "{calls}"\n'
        f'case "$*" in *" ps "*) echo {health} ;; esac',
        "uv": f'echo "uv $*" >> "{calls}"',
        "sleep": "true",
        "openssl": f"exit {handshake}",
    }
    for stub_name, body in stubs.items():
        stub = scratch / "bin" / stub_name
        stub.write_text(f"#!/bin/sh\n{body}\n")
        stub.chmod(0o755)

    result = subprocess.run(
        ["bash", str(scratch / "scripts" / "start.sh")],
        capture_output=True,
        text=True,
        cwd=scratch,
        env={"PATH": f"{scratch / 'bin'}:/usr/bin:/bin", "HOME": str(scratch)},
    )
    recorded = calls.read_text() if calls.exists() else ""
    shutil.rmtree(scratch, ignore_errors=True)
    return result, recorded


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

    def test_a_name_with_a_space_does_not_stop_it(self) -> None:
        """An unquoted `Jane Doe` in `.env` is not shell code to run."""
        result, recorded = _run_in_scratch(
            "start_with_spaces",
            "POSTGRESQL_USER=appuser\nRABBITMQ_PASSWORD=secret\n"
            "FIRST_SUPERUSER_FULL_NAME=Jane Doe\n",
        )

        assert result.returncode == 0, result.stderr
        assert "command not found" not in result.stderr
        assert "pg_isready -q -U appuser" in recorded
        assert "uv run fastapi dev" in recorded

    def test_the_broker_is_started_before_the_server(self) -> None:
        """Without it every request-path `.delay()` fails after its retries."""
        result, recorded = _run_in_scratch(
            "start_with_broker", "POSTGRESQL_USER=appuser\nRABBITMQ_PASSWORD=secret\n"
        )

        assert result.returncode == 0, result.stderr
        assert "up -d postgresql redis rabbitmq" in recorded

    def test_a_broker_that_never_turns_healthy_stops_the_start(self) -> None:
        """Serving without a broker fails quietly on every dispatch."""
        result, recorded = _run_in_scratch(
            "start_unhealthy_broker",
            "POSTGRESQL_USER=appuser\nRABBITMQ_PASSWORD=secret\n",
            health="starting",
        )

        assert result.returncode == 1
        assert "logs rabbitmq" in result.stderr
        assert "uv run fastapi dev" not in recorded

    def test_a_broker_failing_the_tls_handshake_stops_the_start(self) -> None:
        """A healthy ping says nothing about the certificates AMQPS uses."""
        result, recorded = _run_in_scratch(
            "start_bad_handshake",
            "POSTGRESQL_USER=appuser\nRABBITMQ_PASSWORD=secret\n",
            handshake=1,
        )

        assert result.returncode == 1
        assert "TLS handshake" in result.stderr
        assert "uv run fastapi dev" not in recorded

    def test_an_empty_broker_password_points_at_setup(self) -> None:
        """RabbitMQ will not start without one, and the value is not echoed."""
        result, recorded = _run_in_scratch(
            "start_without_password", "POSTGRESQL_USER=appuser\nRABBITMQ_PASSWORD=\n"
        )

        assert result.returncode == 1
        assert "RABBITMQ_PASSWORD" in result.stderr
        assert "setup.sh" in result.stderr
        assert "docker" not in recorded

    def test_a_missing_certificate_points_at_setup(self) -> None:
        """The broker listens on TLS only, so it needs the certificates."""
        result, recorded = _run_in_scratch(
            "start_without_certificate",
            "POSTGRESQL_USER=appuser\nRABBITMQ_PASSWORD=secret\n",
            certificate=False,
        )

        assert result.returncode == 1
        assert "ca_certificate.pem" in result.stderr
        assert "setup.sh" in result.stderr
        assert "docker" not in recorded

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
