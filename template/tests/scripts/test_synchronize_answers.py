"""Tests that a sync can recover the answers it reconstructs.

`scripts/synchronize.sh` rebuilds `.copier/.answers.yml` from the project's
own files on every run, because the real answers file holds secrets and is
deleted afterwards. That only works while every answer is actually readable
back out of a committed file. Three were not, so each sync quietly rewrote
them to a default and then re-rendered the project from that.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENT_EXAMPLE = PROJECT_ROOT / ".env.example"
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.app.yml"
SYNCHRONIZE = PROJECT_ROOT / "scripts" / "synchronize.sh"


def _environment_keys() -> set[str]:
    """Every key assigned in the generated `.env.example`.

    Returns:
        The bare key names, without values.
    """
    return {
        match.group(1)
        for match in re.finditer(
            r"^([A-Z0-9_]+)=", ENVIRONMENT_EXAMPLE.read_text(), re.MULTILINE
        )
    }


class TestAnswersAreRecoverable:
    """Each reconstructed answer has to be present in a committed file."""

    def test_timezone_is_emitted(self) -> None:
        """`TIMEZONE` used to sit inside the Traefik block only.

        Without Traefik the key was absent, so the sync fell through to its
        `UTC` fallback and reset the answer on every run.
        """
        assert "TIMEZONE" in _environment_keys()

    def test_storage_region_is_emitted(self) -> None:
        """`STORAGE_REGION` was asked for and then never written anywhere."""
        assert "STORAGE_REGION" in _environment_keys()

    def test_storage_region_answer_reaches_settings(self) -> None:
        """The answer has to be what the application actually uses."""
        from app.core.settings import Settings

        region = re.search(
            r"^STORAGE_REGION=(.*)$", ENVIRONMENT_EXAMPLE.read_text(), re.MULTILINE
        )
        assert region is not None

        default = Settings.model_fields["STORAGE_REGION"].default
        assert default == region.group(1).strip()


class TestRedbeatIsInferredFromCompose:
    """`include_redbeat` has to be read from something that varies with it."""

    def test_pyproject_cannot_answer_the_question(self) -> None:
        """`celery-redbeat` is an unconditional dependency.

        This is why the old `grep redbeat pyproject.toml` flipped every
        project to `include_redbeat: true` on its first sync.
        """
        assert "redbeat" in (PROJECT_ROOT / "pyproject.toml").read_text()

    def test_compose_reflects_the_chosen_scheduler(self) -> None:
        """The compose command names RedBeat only when it is in use."""
        compose = COMPOSE_FILE.read_text()
        uses_redbeat = "RedBeatScheduler" in compose

        # The two branches are mutually exclusive, whichever was rendered.
        assert uses_redbeat != ("beat --loglevel=info" in compose.replace('", "', " "))

    def test_the_script_reads_compose_not_pyproject(self) -> None:
        """Guard the inference itself, not just the files it reads."""
        script = SYNCHRONIZE.read_text()
        line = next(
            line for line in script.splitlines() if line.startswith("include_redbeat=")
        )

        assert "docker-compose.app.yml" in line
        assert "pyproject.toml" not in line
