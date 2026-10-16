"""Tests that `setup.sh` adds settings a template update introduced.

`.env` is made once, from `.env.example`. A setting added to the template
afterwards, one a compose file requires, never reached it, and
`docker compose` refused to start even the database on that machine.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

SETUP = Path(__file__).resolve().parents[2] / "scripts" / "setup.sh"


def _add_missing_settings(example: str, target: str | None, tmp_path: Path) -> str:
    """Run `setup.sh`'s own function over two files.

    Args:
        example: The contents of `.env.example`.
        target: The contents of `.env`, or None for an empty file.
        tmp_path: Per-test temporary directory.

    Returns:
        The contents of `.env` afterwards.
    """
    text = SETUP.read_text()
    match = re.search(
        r"^add_missing_settings\(\) \{\n.*?^\}$", text, re.MULTILINE | re.DOTALL
    )
    assert match is not None, "could not find add_missing_settings() in setup.sh"
    (tmp_path / ".env.example").write_text(example)
    (tmp_path / ".env").write_text(target or "")
    subprocess.run(
        [
            "bash",
            "-c",
            f'info() {{ :; }}\n{match.group(0)}\nadd_missing_settings "$1" "$2"',
            "harness",
            str(tmp_path / ".env.example"),
            str(tmp_path / ".env"),
        ],
        check=True,
    )
    return (tmp_path / ".env").read_text()


class TestAddMissingSettings:
    """New settings arrive, existing values stay."""

    def test_adds_a_setting_the_env_lacks(self, tmp_path: Path) -> None:
        """A setting only the example has is appended as the example has it."""
        result = _add_missing_settings(
            'PORT=1\nAPP_DOMAIN="localhost"\n', "PORT=2\n", tmp_path
        )

        assert result == 'PORT=2\nAPP_DOMAIN="localhost"\n'

    def test_keeps_values_already_set(self, tmp_path: Path) -> None:
        """A setting both files have keeps the value in `.env`, even empty."""
        result = _add_missing_settings(
            "SECRET_KEY=your-secret-key\nSENTRY_DSN=x\n",
            "SECRET_KEY=real\nSENTRY_DSN=\n",
            tmp_path,
        )

        assert result == "SECRET_KEY=real\nSENTRY_DSN=\n"

    def test_skips_comments_and_template_lines(self, tmp_path: Path) -> None:
        """Only `KEY=value` lines count, a comment naming one does not."""
        result = _add_missing_settings(
            "# TILE_UPSTREAM=example\n\nTILE_UPSTREAM=a\n", None, tmp_path
        )

        assert result == "TILE_UPSTREAM=a\n"

    def test_starts_on_a_new_line(self, tmp_path: Path) -> None:
        """A `.env` without a final newline does not swallow the new key."""
        result = _add_missing_settings("A=1\nB=2\n", "A=3", tmp_path)

        assert result == "A=3\nB=2\n"
