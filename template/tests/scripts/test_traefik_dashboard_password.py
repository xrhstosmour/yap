"""Tests that the Traefik dashboard password is recoverable.

`assemble.py` generated it, hashed it into the vendored `.htpasswd`, and
then let it go out of scope. The dashboard was protected by a password that
had never been written anywhere a person could read, so nobody could log in
and there was no way to find out what it was.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
ASSEMBLE = SCRIPTS / "assemble.py"
SETUP = SCRIPTS / "setup.sh"

KEY = "TRAEFIK_DASHBOARD_PASSWORD"


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


class TestThePasswordIsPersisted:
    """Generating it is only useful if it is also written down."""

    def test_it_lands_in_the_environment_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A generated password is stored, not discarded.

        Args:
            tmp_path: Per-test working directory.
            monkeypatch: Fixture used to change directory.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("SECRET_KEY=x\n")

        module = _assemble()
        password = module.traefik_dashboard_password("demo")
        module.store_traefik_dashboard_password(password)

        assert f"{KEY}={password}" in (tmp_path / ".env").read_text()

    def test_an_existing_key_is_replaced_not_duplicated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Repeated runs must not pile up duplicate lines.

        Args:
            tmp_path: Per-test working directory.
            monkeypatch: Fixture used to change directory.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text(f"{KEY}=old\nSECRET_KEY=x\n")

        _assemble().store_traefik_dashboard_password("new")

        content = (tmp_path / ".env").read_text()
        assert content.count(f"{KEY}=") == 1
        assert f"{KEY}=new" in content

    def test_it_falls_back_to_the_example_before_env_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`assemble.py` runs before `setup.sh` creates `.env`.

        Args:
            tmp_path: Per-test working directory.
            monkeypatch: Fixture used to change directory.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env.example").write_text(f"{KEY}=\n")

        _assemble().store_traefik_dashboard_password("generated")

        assert f"{KEY}=generated" in (tmp_path / ".env.example").read_text()


class TestThePasswordIsStable:
    """A re-assemble must not silently change the working password."""

    def test_an_existing_password_is_reused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`copier update` re-vendors `.htpasswd` back to its placeholder.

        Args:
            tmp_path: Per-test working directory.
            monkeypatch: Fixture used to change directory.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text(f"{KEY}=already-in-use\n")

        assert _assemble().traefik_dashboard_password("demo") == "already-in-use"

    def test_a_placeholder_is_not_reused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The scrubbed value in `.env.example` is not a real password.

        Args:
            tmp_path: Per-test working directory.
            monkeypatch: Fixture used to change directory.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env.example").write_text(
            f"{KEY}=your-traefik-dashboard-password\n"
        )

        password = _assemble().traefik_dashboard_password("demo")

        assert password.startswith("demo-")
        assert not password.startswith("your-")

    def test_a_fresh_password_is_not_trivially_guessable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It guards a dashboard reachable over the network.

        Args:
            tmp_path: Per-test working directory.
            monkeypatch: Fixture used to change directory.
        """
        monkeypatch.chdir(tmp_path)
        module = _assemble()

        first = module.traefik_dashboard_password("demo")
        second = module.traefik_dashboard_password("demo")

        assert first != second
        assert len(first) > len("demo-") + 8


class TestThePlaintextIsNotCommitted:
    """`.env.example` is committed, `.env` is not."""

    def test_setup_scrubs_the_example(self) -> None:
        """The scrub list has to cover this key like every other secret."""
        assert f"s/{KEY}=.*/{KEY}=your-" in SETUP.read_text()
