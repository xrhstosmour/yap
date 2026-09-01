"""Tests that files holding this project's secrets are not world readable.

`.env` carries the JWT signing key, the Fernet key and every service
password, permanently. `.copier/.answers.yml`, rebuilt by `synchronize.sh`
on every run, carries the same set for the length of a `copier update`.
Both were created at 0644 by `cp` and `cat >` under the default umask.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
SETUP = SCRIPTS / "setup.sh"
SYNCHRONIZE = SCRIPTS / "synchronize.sh"


class TestScriptsTightenPermissions:
    """Each script has to lock down the file it writes."""

    def test_setup_locks_down_the_environment_file(self) -> None:
        """`.env` outlives the run, so its mode matters most."""
        assert re.search(r"^chmod 600 \.env$", SETUP.read_text(), re.MULTILINE)

    def test_setup_tightens_an_existing_environment_file(self) -> None:
        """A project created before this change gets fixed on its next run.

        The `chmod` has to sit outside the `if [ ! -f .env ]` branch, or it
        only ever applies to a freshly created file.
        """
        text = SETUP.read_text()
        branch = text.index("if [ ! -f .env ]; then")
        closing = text.index("\nfi\n", branch)
        chmod = text.index("chmod 600 .env")

        assert chmod > closing

    def test_synchronize_locks_down_the_answers_file(self) -> None:
        """It carries the same secrets for the length of an update."""
        assert re.search(
            r"^chmod 600 \.copier/\.answers\.yml$",
            SYNCHRONIZE.read_text(),
            re.MULTILINE,
        )

    def test_the_answers_file_is_locked_before_secrets_are_written(self) -> None:
        """Writing first and chmod-ing after would leave a 0644 window."""
        text = SYNCHRONIZE.read_text()

        assert text.index("chmod 600 .copier/.answers.yml") < text.index(
            "cat > .copier/.answers.yml"
        )


class TestTheTechniqueHolds:
    """The redirect must not undo the mode that was just set."""

    def test_truncating_an_existing_file_keeps_its_mode(self, tmp_path: Path) -> None:
        """This is what lets the file be locked down before it is filled.

        Args:
            tmp_path: Per-test working directory.
        """
        target = tmp_path / "answers.yml"
        subprocess.run(
            [
                "bash",
                "-c",
                f': > "{target}"\n'
                f'chmod 600 "{target}"\n'
                f'cat > "{target}" << YAML\njwt_secret_key: "s"\nYAML\n',
            ],
            check=True,
        )

        assert target.stat().st_mode & 0o077 == 0
        assert "jwt_secret_key" in target.read_text()
