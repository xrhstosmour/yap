"""Tests what `setup.sh` does about a missing uv or Docker.

Copier runs `setup.sh` during `copier copy`. It used to pipe the unpinned
`get.docker.com` script into a root shell on Linux, with its errors hidden,
and the unversioned uv installer everywhere.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

SETUP = Path(__file__).resolve().parents[2] / "scripts" / "setup.sh"


def _run_without(
    tmp_path: Path, *, has_uv: bool
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run `setup.sh` on a `PATH` without Docker, recording every `curl` call.

    Args:
        tmp_path: Per-test temporary directory, the project root.
        has_uv: Whether a `uv` is on the `PATH`.

    Returns:
        The finished process, and the `curl` calls it made.
    """
    (tmp_path / "scripts").mkdir()
    shutil.copy(SETUP, tmp_path / "scripts" / "setup.sh")
    binary = tmp_path / "bin"
    binary.mkdir()
    calls = tmp_path / "curl-calls"
    curl = binary / "curl"
    curl.write_text(f'#!/bin/sh\necho "$*" >> "{calls}"\n')
    curl.chmod(0o755)
    if has_uv:
        uv = binary / "uv"
        uv.write_text("#!/bin/sh\nexit 0\n")
        uv.chmod(0o755)
    python = shutil.which("python3")
    assert python is not None
    (binary / "python3").symlink_to(python)
    # CI runners ship Docker in `/usr/bin`, so the system tools are linked in
    # one by one, leaving Docker out, instead of putting `/usr/bin` on the
    # `PATH`.
    for directory in (Path("/usr/bin"), Path("/bin")):
        for tool in directory.iterdir():
            link = binary / tool.name
            if tool.name != "docker" and not link.exists() and not link.is_symlink():
                link.symlink_to(tool)
    result = subprocess.run(
        ["bash", str(tmp_path / "scripts" / "setup.sh")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={"PATH": str(binary), "HOME": str(tmp_path)},
    )
    return result, calls.read_text() if calls.exists() else ""


class TestMissingDocker:
    """Docker is a prerequisite, never fetched and run as root."""

    def test_it_stops_with_where_to_get_it(self, tmp_path: Path) -> None:
        """Without Docker there is nothing to bring the stack up with."""
        result, calls = _run_without(tmp_path, has_uv=True)

        assert result.returncode == 1
        assert "Docker is required" in result.stdout
        assert "docs.docker.com" in result.stdout
        assert calls == ""


class TestMissingUv:
    """uv is installed, but from a pinned release."""

    def test_the_installer_is_versioned(self, tmp_path: Path) -> None:
        """The unversioned installer serves whatever is newest that day."""
        _, calls = _run_without(tmp_path, has_uv=False)

        assert "https://astral.sh/uv/install.sh" not in calls
        assert "https://astral.sh/uv/0." in calls
