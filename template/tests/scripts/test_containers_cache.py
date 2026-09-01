"""Tests the cache `assemble.py` vendors the containers repo through."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

ASSEMBLE = Path(__file__).resolve().parents[2] / "scripts" / "assemble.py"


def _assemble() -> ModuleType:
    """Import `scripts/assemble.py` as a module.

    Returns:
        The imported module.
    """
    spec = importlib.util.spec_from_file_location("assemble", ASSEMBLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestCacheIsPerUser:
    """A fixed path under `/tmp` is not somewhere to trust code from.

    `/tmp` is world writable, so on a shared machine any local user could
    pre-create `/tmp/containers`. `assemble.py` vendored whatever it found
    there into the project's compose files, which then run with the
    project's secrets, and nothing checked what it was.
    """

    def test_the_cache_is_not_in_a_shared_directory(self) -> None:
        """The cache path must not be the old world-writable one."""
        cache = _assemble().cache_directory()

        assert not cache.startswith("/tmp/")
        assert not cache.startswith("/var/tmp/")

    def test_the_cache_follows_xdg_cache_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A caller that sets `XDG_CACHE_HOME` gets its cache there.

        Args:
            monkeypatch: Fixture used to set the environment.
            tmp_path: Per-test directory standing in for the cache root.
        """
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

        assert _assemble().cache_directory().startswith(str(tmp_path))

    def test_the_cache_falls_back_to_the_home_directory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without `XDG_CACHE_HOME` it lands under the user's home.

        Args:
            monkeypatch: Fixture used to clear the environment.
        """
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        cache = _assemble().cache_directory()

        assert cache.startswith(str(Path.home()))

    def test_no_code_path_still_points_at_the_old_directory(self) -> None:
        """No leftover literal can reintroduce the shared directory.

        Checks string literals in the parsed module, skipping docstrings,
        so the explanation of the old behaviour does not satisfy the test.
        """
        import ast

        tree = ast.parse(ASSEMBLE.read_text())
        docstrings = {
            node.body[0].value
            for node in ast.walk(tree)
            if isinstance(
                node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            )
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }

        literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node not in docstrings
        ]

        assert not [value for value in literals if "/tmp/containers" in value]


class TestCacheIsVerified:
    """Reuse is conditional on the cache being what it claims to be."""

    def test_a_directory_that_is_not_a_checkout_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """This is the planted-directory case.

        Args:
            tmp_path: Per-test directory standing in for a planted cache.
        """
        planted = tmp_path / "containers"
        planted.mkdir()
        (planted / "docker-compose.yml").write_text("services: {}\n")

        assert _assemble().cache_is_current(str(planted)) is False

    def test_a_checkout_at_another_commit_is_rejected(self, tmp_path: Path) -> None:
        """A cache from before a `REPO_COMMIT` bump must not be reused.

        Args:
            tmp_path: Per-test directory holding a throwaway repository.
        """
        stale = tmp_path / "containers"
        stale.mkdir()
        for command in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "test@example.com"],
            ["git", "config", "user.name", "test"],
            ["git", "commit", "-q", "--allow-empty", "-m", "not the pinned commit"],
        ):
            subprocess.run(command, cwd=stale, check=True)

        assert _assemble().cache_is_current(str(stale)) is False

    def test_a_checkout_at_the_pinned_commit_is_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point is that a genuine cache is still reused.

        Args:
            tmp_path: Per-test directory holding a throwaway repository.
            monkeypatch: Fixture used to pin the expected commit.
        """
        current = tmp_path / "containers"
        current.mkdir()
        for command in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "test@example.com"],
            ["git", "config", "user.name", "test"],
            ["git", "commit", "-q", "--allow-empty", "-m", "the pinned commit"],
        ):
            subprocess.run(command, cwd=current, check=True)

        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=current,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        module = _assemble()
        monkeypatch.setattr(module, "REPO_COMMIT", head)

        assert module.cache_is_current(str(current)) is True
