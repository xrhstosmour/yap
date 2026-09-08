"""Tests that `setup.sh` no longer leaves `MINIO_ROOT_USER` fixed.

`MINIO_ROOT_USER` used to stay hardcoded as `minioadmin` while
`MINIO_ROOT_PASSWORD` was randomized, a real admin credential left at its
well-known default halves what an attacker has to guess once the password
alone is unpredictable. It must now be generated and backfilled the same
way as `MINIO_ROOT_PASSWORD`.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

SETUP = Path(__file__).resolve().parents[2] / "scripts" / "setup.sh"


def _function(name: str) -> str:
    """Pull one shell function out of `setup.sh`, see
    `test_setup_secret_substitution.py` for why this tests the function the
    script actually runs rather than a copy of it."""
    text = SETUP.read_text()
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}$", text, re.MULTILINE | re.DOTALL)
    assert match is not None, f"could not find {name}() in setup.sh"
    return match.group(0)


@pytest.fixture
def harness(tmp_path: Path) -> Path:
    """A script exposing `setup.sh`'s `backfill_secret` function."""
    script = tmp_path / "harness.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        f"{_function('set_environment_value')}\n\n"
        f"{_function('backfill_secret')}\n\n"
        'cd "$(dirname "$1")"\n'
        "shift\n"
        '"$@"\n'
    )
    return script


def _call(
    harness: Path, env_file: Path, *arguments: str
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(harness), str(env_file), *arguments],
        capture_output=True,
        text=True,
    )


class TestSetupGeneratesAMinioRootUser:
    """`setup.sh` itself must generate and backfill the username."""

    def test_generation_line_exists(self) -> None:
        """A random value is generated when none is supplied, matching the
        treatment already given to `MINIO_ROOT_PASSWORD`."""
        text = SETUP.read_text()
        assert re.search(
            r'MINIO_ROOT_USER="\$\{MINIO_ROOT_USER:-\$\(python3 -c "import secrets;',
            text,
        )

    def test_backfill_call_exists_with_the_old_default_as_migration_target(
        self,
    ) -> None:
        """Existing installs still on the old `minioadmin` default must be
        migrated too, not just fresh ones."""
        text = SETUP.read_text()
        assert (
            'backfill_secret MINIO_ROOT_USER "${MINIO_ROOT_USER}" "minioadmin"' in text
        )

    def test_backfill_runs_before_the_password_backfill(self) -> None:
        """Order isn't load-bearing today, but keeping the pair adjacent
        keeps the two credentials from drifting apart in a future edit."""
        text = SETUP.read_text()
        user_index = text.index("backfill_secret MINIO_ROOT_USER")
        password_index = text.index("backfill_secret MINIO_ROOT_PASSWORD")
        assert user_index < password_index


class TestBackfillSecretMigratesTheOldDefault:
    """Exercises the actual `backfill_secret` function against a `.env`."""

    def test_the_known_default_is_replaced(self, harness: Path, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("MINIO_ROOT_USER=minioadmin\nOTHER=untouched\n")

        result = _call(
            harness,
            env_file,
            "backfill_secret",
            "MINIO_ROOT_USER",
            "a-random-username",
            "minioadmin",
        )

        assert result.returncode == 0, result.stderr
        assert env_file.read_text() == (
            "MINIO_ROOT_USER=a-random-username\nOTHER=untouched\n"
        )

    def test_a_deliberately_chosen_username_is_left_alone(
        self, harness: Path, tmp_path: Path
    ) -> None:
        """Only the known default and empty/placeholder values are migrated,
        a value someone already set on purpose must survive."""
        env_file = tmp_path / ".env"
        env_file.write_text("MINIO_ROOT_USER=our-own-admin\n")

        result = _call(
            harness,
            env_file,
            "backfill_secret",
            "MINIO_ROOT_USER",
            "a-random-username",
            "minioadmin",
        )

        assert result.returncode == 0, result.stderr
        assert env_file.read_text() == "MINIO_ROOT_USER=our-own-admin\n"

    def test_a_missing_key_is_appended(self, harness: Path, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("OTHER=1\n")

        result = _call(
            harness,
            env_file,
            "backfill_secret",
            "MINIO_ROOT_USER",
            "a-random-username",
            "minioadmin",
        )

        assert result.returncode == 0, result.stderr
        assert env_file.read_text() == "OTHER=1\nMINIO_ROOT_USER=a-random-username\n"
