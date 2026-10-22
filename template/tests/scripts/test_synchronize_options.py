"""Tests that `synchronize.sh` can change a generation option.

Every answer is read back from the project's own files, so a sync keeps
the options the project was generated with. The README told people to
re-run the script and answer yes to an option, but it never asks, so
there was no way to turn one on short of generating the project again.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

SYNCHRONIZE = Path(__file__).resolve().parents[2] / "scripts" / "synchronize.sh"


def _run(
    tmp_path: Path, *arguments: str
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run `synchronize.sh` in a bare project, with `copier` recorded.

    `git ls-remote` is answered locally, every other `git` call is real.
    The recorded `copier` fails, so the run stops right after it.

    Args:
        tmp_path: Per-test temporary directory, the project root.
        arguments: Arguments passed to the script.

    Returns:
        The finished process, and the arguments `copier` was called with.
    """
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    shutil.copy(SYNCHRONIZE, project / "scripts" / "synchronize.sh")
    (project / ".env").write_text("SECRET_KEY=test\n")
    (project / ".gitignore").write_text(".env\n.copier/.answers.yml\n")
    # The files every generated project has, the answers are read from them.
    (project / ".env.example").write_text("TIMEZONE=UTC\n")
    (project / "pyproject.toml").write_text('[project]\nname = "probe"\n')
    (project / "scripts" / "start.sh").write_text("PORT=8000\n")
    (project / "docker-compose.yml").write_text("include: []\n")
    (project / "docker-compose.app.yml").write_text("services: {}\n")
    binary = tmp_path / "bin"
    binary.mkdir()
    calls = tmp_path / "copier-calls"
    copier = binary / "copier"
    copier.write_text(f'#!/bin/sh\necho "$*" > "{calls}"\nexit 1\n')
    copier.chmod(0o755)
    # The script reads `pyproject.toml` with `tomllib`, which an older
    # system `python3` lacks.
    (binary / "python3").symlink_to(sys.executable)
    real_git = shutil.which("git")
    assert real_git is not None
    git = binary / "git"
    git.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = ls-remote ]; then printf "%s\\tHEAD\\n" '
        "0123456789abcdef0123456789abcdef01234567; exit 0; fi\n"
        f'exec "{real_git}" "$@"\n'
    )
    git.chmod(0o755)
    environment = {
        "PATH": f"{binary}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    for command in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "Initial"],
    ):
        subprocess.run(command, cwd=project, env=environment, check=True)
    result = subprocess.run(
        ["bash", "scripts/synchronize.sh", *arguments],
        capture_output=True,
        text=True,
        cwd=project,
        env=environment,
    )
    return result, calls.read_text() if calls.exists() else ""


class TestChangingAnOption:
    """`--data` reaches `copier update`, on top of the recovered answers."""

    def test_the_answer_is_passed_to_copier(self, tmp_path: Path) -> None:
        """Copier gives `--data` precedence over the answers file."""
        _, calls = _run(tmp_path, "--data", "include_redbeat=true")

        assert "--data include_redbeat=true" in calls

    def test_a_plain_sync_passes_none(self, tmp_path: Path) -> None:
        """An empty list must expand to nothing, even on Bash 3."""
        _, calls = _run(tmp_path)

        assert "update" in calls
        assert "--data" not in calls

    def test_a_value_without_a_key_is_refused_before_copier(
        self, tmp_path: Path
    ) -> None:
        """A typo should not start a sync with the option silently unset."""
        result, calls = _run(tmp_path, "--data", "include_redbeat")

        assert result.returncode == 1
        assert "key=value" in result.stdout
        assert calls == ""
