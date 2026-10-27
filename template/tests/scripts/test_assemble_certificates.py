"""Tests the narrow `assemble.py --certificates-only` entry point.

`setup.sh` runs it on a fresh clone. The full run copies every vendored
service over local edits and regenerates the `Traefik` secrets, so this one
may write the `RabbitMQ` certificates and nothing else.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ASSEMBLE = Path(__file__).resolve().parents[2] / "scripts" / "assemble.py"
BROKER = Path("containers/distribute/brokers/rabbitmq/certificates")
FLOWER = Path("containers/monitoring/workers/flower")


def _run(project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run `assemble.py` with a project directory as the working directory.

    Args:
        project: The directory standing in for a generated project.
        arguments: Command line arguments for the script.

    Returns:
        The finished process.
    """
    return subprocess.run(
        [sys.executable, str(ASSEMBLE), *arguments],
        capture_output=True,
        text=True,
        cwd=project,
    )


def _files(project: Path) -> dict[str, str]:
    """Map every file below the project to its contents.

    Args:
        project: The project directory.

    Returns:
        Relative path to text, for comparing before and after.
    """
    return {
        str(path.relative_to(project)): path.read_text(errors="replace")
        for path in project.rglob("*")
        if path.is_file()
    }


class TestCertificatesOnly:
    """Only the broker certificates, the path file and Flower's copy change."""

    def test_nothing_else_is_written(self, tmp_path: Path) -> None:
        """A locally edited service directory is left exactly as it was."""
        edited = tmp_path / "containers" / "databases" / "postgresql" / "edit.txt"
        edited.parent.mkdir(parents=True)
        edited.write_text("local edit")
        (tmp_path / FLOWER).mkdir(parents=True)
        before = _files(tmp_path)

        result = _run(tmp_path, "--certificates-only")

        assert result.returncode == 0, result.stderr
        written = {
            path for path, text in _files(tmp_path).items() if before.get(path) != text
        }
        assert written == {
            "containers/.certificates",
            *(
                str(BROKER / name)
                for name in (
                    "ca_key.pem",
                    "ca_certificate.pem",
                    "server_key.pem",
                    "server_request.pem",
                    "server_certificate.pem",
                )
            ),
            str(FLOWER / "certificates" / "ca_certificate.pem"),
        }
        assert (
            tmp_path / FLOWER / "certificates" / "ca_certificate.pem"
        ).read_text() == (tmp_path / BROKER / "ca_certificate.pem").read_text()

    def test_flower_is_left_alone_when_not_vendored(self, tmp_path: Path) -> None:
        """No Flower directory means no Flower files are created."""
        result = _run(tmp_path, "--certificates-only")

        assert result.returncode == 0, result.stderr
        assert not (tmp_path / FLOWER).exists()

    @pytest.mark.parametrize("arguments", [["--certificate-only"], ["certificates"]])
    def test_unknown_arguments_do_not_run_the_full_assembly(
        self, tmp_path: Path, arguments: list[str]
    ) -> None:
        """A typo exits 2 instead of silently re-vendoring every service."""
        result = _run(tmp_path, *arguments)

        assert result.returncode == 2
        assert "usage" in result.stderr.lower()
        assert _files(tmp_path) == {}
