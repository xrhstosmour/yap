"""Tests that `scripts/backup.sh` parses `.env` without executing it."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backup.sh"


def _project(tmp_path: Path, environment: str) -> Path:
    """Lay out a throwaway project with the script and a given `.env`.

    Args:
        tmp_path: Pytest temporary directory.
        environment: Contents to write to `.env`.

    Returns:
        The project directory.
    """
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / "backup.sh")
    (tmp_path / "scripts" / "backup.sh").chmod(0o755)
    (tmp_path / ".env").write_text(environment)
    return tmp_path


def _run(project: Path) -> subprocess.CompletedProcess[str]:
    """Run the backup script in a project directory.

    Args:
        project: Directory laid out by `_project`.

    Returns:
        The completed process.
    """
    # A deliberately bare environment. The script lets real environment
    # variables win over `.env`, which is intended, so inheriting the test
    # runner's POSTGRESQL_* would mask what `.env` parsing actually produced.
    return subprocess.run(
        ["bash", str(project / "scripts" / "backup.sh")],
        capture_output=True,
        text=True,
        cwd=project,
        timeout=60,
        check=False,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(project)},
    )


class TestBackupDoesNotSourceEnvironment:
    """`.env` is data, not shell.

    The script used to `set -a; . .env`, which requires every line to be
    valid bash. `FIRST_SUPERUSER_FULL_NAME` is free text collected from
    the operator, so an ordinary name with a space made bash read the
    second word as a command and `set -e` killed the run. A nightly cron
    backup would stop happening and say nothing.
    """

    def test_a_name_with_a_space_does_not_kill_the_script(self, tmp_path: Path) -> None:
        """The run must reach its own validation, not die parsing `.env`.

        Args:
            tmp_path: Pytest temporary directory.
        """
        project = _project(
            tmp_path,
            "FIRST_SUPERUSER_FULL_NAME=John Doe\nPOSTGRESQL_DATABASE=appdb\n",
        )

        result = _run(project)
        output = result.stdout + result.stderr

        # Its own error, from well past the environment loading.
        assert "POSTGRESQL_PASSWORD not set" in output, output
        assert "command not found" not in output, output

    def test_command_substitution_in_a_value_is_not_executed(
        self, tmp_path: Path
    ) -> None:
        """A `$(...)` in any value must be inert.

        Args:
            tmp_path: Pytest temporary directory.
        """
        marker = tmp_path / "executed"
        project = _project(
            tmp_path,
            f"FIRST_SUPERUSER_FULL_NAME=$(touch {marker})\n"
            "POSTGRESQL_PASSWORD=secret\n"
            "POSTGRESQL_DATABASE=appdb\n",
        )

        _run(project)

        assert not marker.exists()

    def test_backup_directory_is_read_from_the_environment_file(
        self, tmp_path: Path
    ) -> None:
        """A configured `BACKUP_DIRECTORY` must be the one created.

        It used to be resolved before `.env` was loaded, so `mkdir` ran
        against the default and left an empty `backups/` beside the real
        one.

        Args:
            tmp_path: Pytest temporary directory.
        """
        configured = tmp_path / "elsewhere"
        project = _project(
            tmp_path,
            f"BACKUP_DIRECTORY={configured}\n"
            "POSTGRESQL_PASSWORD=secret\n"
            "POSTGRESQL_DATABASE=appdb\n",
        )

        _run(project)

        assert configured.is_dir()
        assert not (project / "backups").exists()
