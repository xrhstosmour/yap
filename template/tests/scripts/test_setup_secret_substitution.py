"""Tests how `setup.sh` writes secret values into `.env`.

Every secret honours a pre-set environment variable, and the values were
written with `sed "s|^${key}=.*|${key}=${value}|"`. `sed` interprets its
replacement text: a bare `&` expands to the whole match, and the delimiter
ends the expression early. Supplying `SMTP_PASSWORD='p&ss|word'` wrote
`SMTP_PASSWORD=pSMTP_PASSWORD=your-smtp-passwordss` into `.env`, and `sed`
exited 0, so the project came up with a password nobody had chosen and no
sign anything had gone wrong.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

SETUP = Path(__file__).resolve().parents[2] / "scripts" / "setup.sh"

AWKWARD_VALUES = [
    "p&ss|word",
    "&",
    "back\\slash",
    "for/ward",
    "a|b|c",
    "plain-secret-1234",
    "s&s|s/s\\s",
]


def _function(name: str) -> str:
    """Pull one shell function out of `setup.sh`.

    Testing the function the script actually runs, rather than a copy of it,
    is the point: the bug was in how it substituted.

    Args:
        name: The function's name.

    Returns:
        Its full definition, including the closing brace.
    """
    text = SETUP.read_text()
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}$", text, re.MULTILINE | re.DOTALL)
    assert match is not None, f"could not find {name}() in setup.sh"
    return match.group(0)


@pytest.fixture
def harness(tmp_path: Path) -> Path:
    """A script exposing `setup.sh`'s substitution functions.

    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        Path to a runnable wrapper taking a function name and its arguments.
    """
    script = tmp_path / "harness.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        f"{_function('set_environment_value')}\n\n"
        f"{_function('set_url_password')}\n\n"
        '"$@"\n'
    )
    return script


def _call(harness: Path, *arguments: str) -> None:
    """Run one of the extracted functions.

    Args:
        harness: The wrapper script.
        *arguments: Function name followed by its arguments.
    """
    result = subprocess.run(
        ["bash", str(harness), *arguments],
        capture_output=True,
        text=True,
        cwd=harness.parent,
    )
    assert result.returncode == 0, result.stderr


class TestPlainValues:
    """A value is a value, not a pattern."""

    @pytest.mark.parametrize("value", AWKWARD_VALUES)
    def test_the_value_is_written_verbatim(
        self, harness: Path, tmp_path: Path, value: str
    ) -> None:
        """Anything a password manager can produce has to survive intact.

        Args:
            harness: The wrapper script.
            tmp_path: Per-test temporary directory.
            value: The secret being written.
        """
        target = tmp_path / ".env"
        target.write_text("SMTP_PASSWORD=your-smtp-password\nOTHER=untouched\n")

        _call(harness, "set_environment_value", "SMTP_PASSWORD", value, str(target))

        assert target.read_text() == f"SMTP_PASSWORD={value}\nOTHER=untouched\n"

    def test_only_the_named_key_is_rewritten(
        self, harness: Path, tmp_path: Path
    ) -> None:
        """A key whose name is a prefix of another must not be confused.

        Args:
            harness: The wrapper script.
            tmp_path: Per-test temporary directory.
        """
        target = tmp_path / ".env"
        target.write_text("REDIS_PASSWORD=old\nREDIS_PASSWORD_EXTRA=keep\n")

        _call(harness, "set_environment_value", "REDIS_PASSWORD", "new", str(target))

        assert target.read_text() == "REDIS_PASSWORD=new\nREDIS_PASSWORD_EXTRA=keep\n"

    def test_a_missing_key_is_appended(self, harness: Path, tmp_path: Path) -> None:
        """A template sync can introduce a key this `.env` has never had.

        Args:
            harness: The wrapper script.
            tmp_path: Per-test temporary directory.
        """
        target = tmp_path / ".env"
        target.write_text("EXISTING=1\n")

        _call(harness, "set_environment_value", "NEW_KEY", "value", str(target))

        assert target.read_text() == "EXISTING=1\nNEW_KEY=value\n"


class TestUrlPasswords:
    """GlitchTip carries the Postgres and Redis passwords inside full URLs."""

    @pytest.mark.parametrize("password", AWKWARD_VALUES)
    def test_the_database_password_is_written_verbatim(
        self, harness: Path, tmp_path: Path, password: str
    ) -> None:
        """The user, host, port and database name all have to survive.

        Args:
            harness: The wrapper script.
            tmp_path: Per-test temporary directory.
            password: The password being written.
        """
        target = tmp_path / ".env"
        target.write_text(
            "DATABASE_URL=postgres://appuser:old@postgresql:5432/glitchtip\n"
        )

        _call(harness, "set_url_password", "DATABASE_URL", password, str(target))

        assert target.read_text() == (
            f"DATABASE_URL=postgres://appuser:{password}@postgresql:5432/glitchtip\n"
        )

    @pytest.mark.parametrize("password", AWKWARD_VALUES)
    def test_the_redis_password_is_written_verbatim(
        self, harness: Path, tmp_path: Path, password: str
    ) -> None:
        """Redis URLs carry no user, just an empty slot before the colon.

        Args:
            harness: The wrapper script.
            tmp_path: Per-test temporary directory.
            password: The password being written.
        """
        target = tmp_path / ".env"
        target.write_text("REDIS_URL=redis://:old@redis:6379/0\n")

        _call(harness, "set_url_password", "REDIS_URL", password, str(target))

        assert target.read_text() == f"REDIS_URL=redis://:{password}@redis:6379/0\n"

    def test_an_at_sign_in_the_old_password_does_not_shift_the_host(
        self, harness: Path, tmp_path: Path
    ) -> None:
        """The host never contains an `@`, so the last one is the separator.

        Args:
            harness: The wrapper script.
            tmp_path: Per-test temporary directory.
        """
        target = tmp_path / ".env"
        target.write_text("DATABASE_URL=postgres://appuser:o@ld@postgresql:5432/db\n")

        _call(harness, "set_url_password", "DATABASE_URL", "new", str(target))

        assert target.read_text() == (
            "DATABASE_URL=postgres://appuser:new@postgresql:5432/db\n"
        )


class TestRemainingSedCalls:
    """`sed` is still fine for the fixed placeholder scrub."""

    def test_no_sed_replacement_interpolates_a_variable(self) -> None:
        """That is the shape that let a secret reach `sed` as a pattern."""
        offenders = [
            line.strip()
            for line in SETUP.read_text().splitlines()
            if "SED_INPLACE[@]" in line
            and "${" in line.split('"${SED_INPLACE[@]}"', 1)[1]
        ]

        assert offenders == []
