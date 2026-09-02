"""Tests how `synchronize.sh` decides whether the sync changed anything.

It branched on `if git diff --stat; then`. `git diff` exits 0 whether or
not anything differs, only `--exit-code` changes that, so the `else` branch
was unreachable: a sync that changed nothing printed an empty stat and then
told the user to review and commit it. `git diff` also never reports
untracked files, and a template update mostly *adds* files, so the one part
of the sync worth looking at was the part it could not see.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

SYNCHRONIZE = Path(__file__).resolve().parents[2] / "scripts" / "synchronize.sh"


def _detection_command() -> str:
    """Pull the change-detection command out of the script.

    Testing the command the script actually runs, rather than a copy of it,
    is the point: the bug was in that command.

    Returns:
        The shell command whose output decides whether anything changed.
    """
    text = SYNCHRONIZE.read_text()
    # The second pattern is the shape this test exists to reject. Matching
    # it too keeps the behavioural cases below meaningful against the old
    # code instead of collapsing into "expression not found".
    for pattern in (
        r"\n\s*changes=\$\((.+?)\)\n",
        r"Checking for changes\.\.\.\"\n\s*if (.+?); then",
    ):
        match = re.search(pattern, text)
        if match is not None:
            return match.group(1)
    raise AssertionError("could not find the change detection in synchronize.sh")


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """A git repository with one committed file.

    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        The repository root, clean at the point the fixture returns.
    """
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    (tmp_path / "tracked.txt").write_text("original\n")
    for command in (
        ["git", "init", "--initial-branch=main", "."],
        ["git", "add", "-A"],
        ["git", "commit", "--no-verify", "-m", "baseline"],
    ):
        subprocess.run(
            command, cwd=tmp_path, env=environment, check=True, capture_output=True
        )
    return tmp_path


def _detected(repository: Path) -> str:
    """Run the script's own detection in a repository.

    Args:
        repository: The repository to inspect.

    Returns:
        What the detection reports, stripped.
    """
    result = subprocess.run(
        ["sh", "-c", _detection_command()],
        cwd=repository,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(repository)},
    )
    return result.stdout.strip()


class TestChangeDetection:
    """What the script counts as a change."""

    def test_a_clean_tree_reports_nothing(self, repository: Path) -> None:
        """Otherwise every sync ends by asking for a commit that has no content.

        Args:
            repository: A clean git repository.
        """
        assert _detected(repository) == ""

    def test_a_modified_file_is_reported(self, repository: Path) -> None:
        """The ordinary case, a template file the update rewrote.

        Args:
            repository: A clean git repository.
        """
        (repository / "tracked.txt").write_text("updated\n")

        assert "tracked.txt" in _detected(repository)

    def test_an_added_file_is_reported(self, repository: Path) -> None:
        """This is the case `git diff` could not see.

        A template update mostly adds files. `git diff` ignores untracked
        paths entirely, so a sync that pulled in a whole new module looked
        identical to a sync that did nothing.

        Args:
            repository: A clean git repository.
        """
        (repository / "brand_new.py").write_text("# added by the template.\n")

        assert "brand_new.py" in _detected(repository)


class TestBranchShape:
    """The branch around the detection."""

    def test_the_decision_is_not_an_exit_status_of_git_diff(self) -> None:
        """`git diff` exits 0 either way, so it can never gate anything."""
        text = SYNCHRONIZE.read_text()

        assert "if git diff --stat; then" not in text

    def test_the_up_to_date_branch_is_reachable(self) -> None:
        """It only helps if the script can actually get there."""
        text = SYNCHRONIZE.read_text()

        assert 'elif [ -n "$changes" ]; then' in text
        assert "No changes, project is up to date." in text
