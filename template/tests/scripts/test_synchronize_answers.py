"""Tests that a sync can recover the answers it reconstructs.

`scripts/synchronize.sh` rebuilds `.copier/.answers.yml` from the project's
own files on every run, because the real answers file holds secrets and is
deleted afterwards. That only works while every answer is actually readable
back out of a committed file. Three were not, so each sync quietly rewrote
them to a default and then re-rendered the project from that.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENT_EXAMPLE = PROJECT_ROOT / ".env.example"
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.app.yml"
SYNCHRONIZE = PROJECT_ROOT / "scripts" / "synchronize.sh"
START = PROJECT_ROOT / "scripts" / "start.sh"


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
    """`include_redbeat` has to be read from what decides the scheduler.

    The original bug was `grep redbeat pyproject.toml`, where the dependency
    was unconditional, so every project flipped to `include_redbeat: true` on
    its first sync. The dependency is gated now, so that grep would happen to
    work, but the Compose command is still the authority: it carries the `-S`
    flag that picks the scheduler beat actually runs.
    """

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


def _development_port_expression() -> str:
    """Pull the port reader out of `synchronize.sh`.

    Running the expression the script actually runs, rather than a copy of it,
    is the point: a reader that no longer matches is the failure.

    Returns:
        The two shell lines that resolve the answer.
    """
    lines = [
        line
        for line in SYNCHRONIZE.read_text().splitlines()
        if line.startswith("development_port=")
    ]
    assert lines, "synchronize.sh does not resolve a development port"
    return "\n".join(lines)


class TestDevelopmentPortSurvivesASync:
    """`development_port` has no `.env` entry, so it is read back from a script.

    A downstream project serving on a port of its own had it rewritten to the
    template default by the next sync, which left its smoke walk and its
    mobile client pointed at a server that was no longer there.
    """

    def test_the_answer_is_emitted(self) -> None:
        """An answer left out is an answer re-asked and defaulted."""
        assert "development_port: $development_port" in SYNCHRONIZE.read_text()

    def test_the_answer_is_read_from_the_start_script(self) -> None:
        """Guard the source, not only that something is emitted."""
        assert "scripts/start.sh" in _development_port_expression()

    def test_the_expression_recovers_a_port_that_is_not_the_fallback(self) -> None:
        """A port equal to the fallback would pass with no reader at all.

        This is the case the whole answer exists for, a project serving
        somewhere other than where the template would have put it.
        """
        with tempfile.TemporaryDirectory() as directory:
            scripts = Path(directory) / "scripts"
            scripts.mkdir()
            (scripts / "start.sh").write_text("#!/usr/bin/env bash\nPORT=8123\n")

            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    f'{_development_port_expression()}\necho "$development_port"',
                ],
                capture_output=True,
                text=True,
                cwd=directory,
                check=True,
            )

        assert result.stdout.strip() == "8123"

    def test_the_expression_recovers_the_port_this_project_serves(self) -> None:
        """The generated project is the one a sync actually reads."""
        declared = re.search(r"^PORT=(\d+)$", START.read_text(), re.MULTILINE)
        assert declared is not None

        result = subprocess.run(
            [
                "bash",
                "-c",
                f'{_development_port_expression()}\necho "$development_port"',
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            check=True,
        )
        assert result.stdout.strip() == declared.group(1)

    def test_a_project_without_the_script_still_answers(self) -> None:
        """A project generated before `start.sh` existed syncs to the default."""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    f'{_development_port_expression()}\necho "$development_port"',
                ],
                capture_output=True,
                text=True,
                cwd=directory,
                check=True,
            )

        assert result.stdout.strip() == "8000"
