"""Tests that the beat scheduler and its dependency agree with each other.

`celery-redbeat` was an unconditional dependency while `include_redbeat`
gated only the Compose command. Importing RedBeat registers a `beat_init`
handler that reads `scheduler.lock_key`, which exists only on
`RedBeatScheduler`, so every project on the default scheduler logged
`AttributeError: 'PersistentScheduler' object has no attribute 'lock_key'`
on each beat start. Celery swallows that exception, so it was noise rather
than breakage, but it advertised a distributed lock that was never taken.

The two halves have to move together: the dependency, the `redbeat_redis_url`
configuration and the `-S redbeat.RedBeatScheduler` flag are one decision.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml

PROJECT = Path(__file__).resolve().parents[2]
CELERY_APP = PROJECT / "app" / "celery_app.py"
COMPOSE = PROJECT / "docker-compose.app.yml"
README = PROJECT / "README.md"


def _dependencies() -> list[str]:
    """The project's runtime dependencies.

    Returns:
        The `project.dependencies` list from `pyproject.toml`.
    """
    configuration = tomllib.loads((PROJECT / "pyproject.toml").read_text())
    dependencies: list[str] = configuration["project"]["dependencies"]
    return dependencies


def _beat_command() -> list[str]:
    """The command the `celery-beat` Compose service runs.

    Returns:
        The command as a list of arguments.
    """
    compose = yaml.safe_load(COMPOSE.read_text())
    command = compose["services"]["celery-beat"]["command"]
    return command if isinstance(command, list) else command.split()


@pytest.fixture(scope="module")
def redbeat_selected() -> bool:
    """Whether this project was generated with RedBeat.

    Read from the Compose command rather than from the Copier answers,
    because the command is what actually decides which scheduler runs.

    Returns:
        True when beat is started with the RedBeat scheduler.
    """
    return "redbeat.RedBeatScheduler" in _beat_command()


class TestSchedulerAndDependencyAgree:
    """One decision, three places that have to reflect it."""

    def test_the_dependency_matches_the_scheduler(self, redbeat_selected: bool) -> None:
        """Installed and unused is what caused the startup `AttributeError`.

        Args:
            redbeat_selected: Whether beat runs the RedBeat scheduler.
        """
        installed = any(
            dependency.startswith("celery-redbeat") for dependency in _dependencies()
        )

        assert installed == redbeat_selected

    def test_the_redis_url_is_configured_only_when_used(
        self, redbeat_selected: bool
    ) -> None:
        """`redbeat_redis_url` on the default scheduler means nothing.

        Args:
            redbeat_selected: Whether beat runs the RedBeat scheduler.
        """
        configured = "redbeat_redis_url" in CELERY_APP.read_text()

        assert configured == redbeat_selected

    def test_redbeat_is_imported_only_when_used(self, redbeat_selected: bool) -> None:
        """The import itself is what registers the `beat_init` handler.

        Args:
            redbeat_selected: Whether beat runs the RedBeat scheduler.
        """
        imported = "import redbeat" in CELERY_APP.read_text()

        assert imported == redbeat_selected


class TestSchedulerIsDocumented:
    """Which scheduler this project runs, and when to change it."""

    def test_the_feature_list_names_the_scheduler_in_use(
        self, redbeat_selected: bool
    ) -> None:
        """It advertised RedBeat to every project, including the ones without it.

        The body may still mention RedBeat, the default build explains how to
        switch to it. The feature bullet is the claim about what runs here.

        Args:
            redbeat_selected: Whether beat runs the RedBeat scheduler.
        """
        bullets = [
            line
            for line in README.read_text().splitlines()
            if line.startswith("- **RedBeat**") or line.startswith("- **Celery beat**")
        ]

        assert len(bullets) == 1, bullets
        assert bullets[0].startswith("- **RedBeat**") == redbeat_selected

    def test_the_readme_explains_the_periodic_task_setup(self) -> None:
        """Both scheduler choices need their own operating instructions."""
        documentation = README.read_text()

        assert "## Periodic Tasks" in documentation
        assert "beat_schedule" in documentation

    def test_the_single_beat_constraint_is_stated(self, redbeat_selected: bool) -> None:
        """The default scheduler double-fires if it is scaled.

        Args:
            redbeat_selected: Whether beat runs the RedBeat scheduler.
        """
        if redbeat_selected:
            pytest.skip("RedBeat coordinates through Redis, several are fine")

        assert "Run exactly one beat process" in README.read_text()
