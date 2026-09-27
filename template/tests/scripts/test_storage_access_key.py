"""Tests that `setup.sh` generates the object storage access key.

The access key is a real credential rather than a display name. Left at a
well-known default it halves what an attacker has to guess once the
secret alone is unpredictable, so it is generated and backfilled the same
way as `STORAGE_SECRET_KEY`.
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


class TestSetupGeneratesTheAccessKey:
    """`setup.sh` itself must generate and backfill the access key."""

    def test_generation_line_exists(self) -> None:
        """A random value is generated when none is supplied, matching the
        treatment already given to `STORAGE_SECRET_KEY`."""
        text = SETUP.read_text()
        assert re.search(
            r'STORAGE_ACCESS_KEY="\$\{STORAGE_ACCESS_KEY:-\$\(python3 -c "import secrets;',
            text,
        )

    def test_backfill_call_exists(self) -> None:
        """A generated value is no use unless it reaches `.env`."""
        text = SETUP.read_text()
        assert 'backfill_secret STORAGE_ACCESS_KEY "${STORAGE_ACCESS_KEY}"' in text

    def test_the_pair_is_backfilled_together(self) -> None:
        """Order is not load-bearing today, but keeping the key and its
        secret adjacent stops the two drifting apart in a future edit."""
        text = SETUP.read_text()
        secret_index = text.index("backfill_secret STORAGE_SECRET_KEY")
        key_index = text.index("backfill_secret STORAGE_ACCESS_KEY")
        assert abs(key_index - secret_index) < 200


class TestBackfillSecretMigratesTheOldDefault:
    """Exercises the actual `backfill_secret` function against a `.env`."""

    def test_the_known_default_is_replaced(self, harness: Path, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("STORAGE_ACCESS_KEY=the-old-default\nOTHER=untouched\n")

        result = _call(
            harness,
            env_file,
            "backfill_secret",
            "STORAGE_ACCESS_KEY",
            "a-random-access-key",
            "the-old-default",
        )

        assert result.returncode == 0, result.stderr
        assert env_file.read_text() == (
            "STORAGE_ACCESS_KEY=a-random-access-key\nOTHER=untouched\n"
        )

    def test_a_deliberately_chosen_username_is_left_alone(
        self, harness: Path, tmp_path: Path
    ) -> None:
        """Only the known default and empty/placeholder values are migrated,
        a value someone already set on purpose must survive."""
        env_file = tmp_path / ".env"
        env_file.write_text("STORAGE_ACCESS_KEY=our-own-key\n")

        result = _call(
            harness,
            env_file,
            "backfill_secret",
            "STORAGE_ACCESS_KEY",
            "a-random-access-key",
            "the-old-default",
        )

        assert result.returncode == 0, result.stderr
        assert env_file.read_text() == "STORAGE_ACCESS_KEY=our-own-key\n"

    def test_a_missing_key_is_appended(self, harness: Path, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("OTHER=1\n")

        result = _call(
            harness,
            env_file,
            "backfill_secret",
            "STORAGE_ACCESS_KEY",
            "a-random-access-key",
            "the-old-default",
        )

        assert result.returncode == 0, result.stderr
        assert (
            env_file.read_text() == "OTHER=1\nSTORAGE_ACCESS_KEY=a-random-access-key\n"
        )
