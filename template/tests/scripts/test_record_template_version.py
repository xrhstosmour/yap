"""Tests the task that stamps the template commit into `.copier/.version`.

Copier ran this as an inline `python3 -c` one-liner that did
`git ls-remote(...).stdout.split()[0]`. With no network `git ls-remote`
exits 128 and prints nothing, `split()` returns an empty list, and the
index raised `IndexError`. Copier aborts generation on a failing task, so
`copier copy` threw away a fully generated project at its very last step
for anyone offline, behind a proxy, or generating while GitHub was down.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "record_template_version.sh"

PLACEHOLDER = "_src_path: gh:xrhstosmour/yap\n_commit: \n"
RESOLVED_SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A minimal generated project the script can run against.

    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        The project root, holding `scripts/` and `.copier/.version`.
    """
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / SCRIPT.name)
    (tmp_path / ".copier").mkdir()
    (tmp_path / ".copier" / ".version").write_text(PLACEHOLDER)
    return tmp_path


def _stub_git(directory: Path, *, exit_code: int, output: str) -> Path:
    """Put a fake `git` first on `PATH`.

    The real remote is not available to the test suite, and the point of
    the test is what happens when it is not available in production either.

    Args:
        directory: Where to write the stub.
        exit_code: What the stub exits with.
        output: The commit the stub resolves, or empty for no output at all.

    Returns:
        The directory to prepend to `PATH`.
    """
    binary = directory / "bin"
    binary.mkdir(exist_ok=True)
    stub = binary / "git"
    body = "printf '%s'\n" if not output else f"printf '%s\\tHEAD\\n' '{output}'\n"
    stub.write_text(f"#!/bin/sh\n{body}exit {exit_code}\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return binary


def _stub_curl(directory: Path, *, exit_code: int) -> None:
    """Put a fake `curl` next to the `git` stub, answering whether GitHub
    has a commit.

    Args:
        directory: Where the `git` stub lives.
        exit_code: Zero when GitHub has the commit, 22 for a 404.
    """
    binary = directory / "bin"
    binary.mkdir(exist_ok=True)
    stub = binary / "curl"
    stub.write_text(f"#!/bin/sh\nexit {exit_code}\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)


def _run(project: Path, path_entry: Path) -> subprocess.CompletedProcess[str]:
    """Run the script with a doctored `PATH`.

    Args:
        project: The generated project root.
        path_entry: Directory holding the `git` stub.

    Returns:
        The finished process.
    """
    return subprocess.run(
        [str(project / "scripts" / SCRIPT.name)],
        cwd=project,
        capture_output=True,
        text=True,
        env={"PATH": f"{path_entry}:/usr/bin:/bin", "HOME": str(project)},
    )


class TestUnreachableRemote:
    """Generation has to survive a remote it cannot reach."""

    def test_generation_is_not_failed(self, project: Path) -> None:
        """A non-zero task exit aborts the whole `copier copy`.

        Args:
            project: The generated project root.
        """
        path_entry = _stub_git(project, exit_code=128, output="")

        result = _run(project, path_entry)

        assert result.returncode == 0, result.stderr

    def test_the_recorded_commit_is_left_alone(self, project: Path) -> None:
        """Better an unresolved placeholder than a corrupted file.

        Args:
            project: The generated project root.
        """
        path_entry = _stub_git(project, exit_code=128, output="")

        _run(project, path_entry)

        assert (project / ".copier" / ".version").read_text() == PLACEHOLDER

    def test_the_user_is_told(self, project: Path) -> None:
        """Silently skipping leaves `synchronize.sh` broken with no clue why.

        Args:
            project: The generated project root.
        """
        path_entry = _stub_git(project, exit_code=128, output="")

        result = _run(project, path_entry)

        assert "Could not reach" in result.stdout
        assert "synchronize.sh" in result.stdout

    def test_an_empty_answer_counts_as_unreachable(self, project: Path) -> None:
        """A proxy can return success with nothing behind it.

        Args:
            project: The generated project root.
        """
        path_entry = _stub_git(project, exit_code=0, output="")

        result = _run(project, path_entry)

        assert result.returncode == 0, result.stderr
        assert (project / ".copier" / ".version").read_text() == PLACEHOLDER


class TestReachableRemote:
    """The happy path still has to work."""

    def test_the_resolved_commit_is_recorded(self, project: Path) -> None:
        """`synchronize.sh` reads this value back to know what to update from.

        Args:
            project: The generated project root.
        """
        path_entry = _stub_git(project, exit_code=0, output=RESOLVED_SHA)

        result = _run(project, path_entry)

        assert result.returncode == 0, result.stderr
        version = (project / ".copier" / ".version").read_text()
        assert f"_commit: {RESOLVED_SHA}" in version
        assert "_src_path: gh:xrhstosmour/yap" in version

    def test_no_backup_file_is_left_behind(self, project: Path) -> None:
        """`sed -i` needs a suffix to be portable, the artefact is not wanted.

        Args:
            project: The generated project root.
        """
        path_entry = _stub_git(project, exit_code=0, output=RESOLVED_SHA)

        _run(project, path_entry)

        assert not (project / ".copier" / ".version.bak").exists()


class TestRenderedCommit:
    """The commit Copier rendered is the base the next sync updates from."""

    RENDERED = "_src_path: gh:xrhstosmour/yap\n_commit: abc1234\n"

    def test_a_commit_github_has_is_kept(self, project: Path) -> None:
        """Replacing it would skip every template change since it.

        Args:
            project: The generated project root.
        """
        (project / ".copier" / ".version").write_text(self.RENDERED)
        path_entry = _stub_git(project, exit_code=0, output=RESOLVED_SHA)
        _stub_curl(project, exit_code=0)

        result = _run(project, path_entry)

        assert result.returncode == 0, result.stderr
        assert (project / ".copier" / ".version").read_text() == self.RENDERED

    def test_a_commit_github_lacks_falls_back_to_the_remote(
        self, project: Path
    ) -> None:
        """A dirty local render records a commit that exists nowhere.

        Args:
            project: The generated project root.
        """
        (project / ".copier" / ".version").write_text(self.RENDERED)
        path_entry = _stub_git(project, exit_code=0, output=RESOLVED_SHA)
        _stub_curl(project, exit_code=22)

        result = _run(project, path_entry)

        assert result.returncode == 0, result.stderr
        version = (project / ".copier" / ".version").read_text()
        assert f"_commit: {RESOLVED_SHA}" in version

    def test_a_described_commit_keeps_only_its_hash(self, project: Path) -> None:
        """`git describe` output is not a ref GitHub can look up.

        Args:
            project: The generated project root.
        """
        (project / ".copier" / ".version").write_text(
            "_src_path: gh:xrhstosmour/yap\n_commit: fix-commit-12-gabc1234\n"
        )
        path_entry = _stub_git(project, exit_code=0, output=RESOLVED_SHA)
        _stub_curl(project, exit_code=0)

        _run(project, path_entry)

        assert (project / ".copier" / ".version").read_text() == self.RENDERED


class TestMissingVersionFile:
    """A missing file must not fail generation either."""

    def test_generation_is_not_failed(self, project: Path) -> None:
        """`sed` on a file that is not there exits non-zero.

        Args:
            project: The generated project root.
        """
        (project / ".copier" / ".version").unlink()
        path_entry = _stub_git(project, exit_code=0, output=RESOLVED_SHA)

        result = _run(project, path_entry)

        assert result.returncode == 0, result.stderr
