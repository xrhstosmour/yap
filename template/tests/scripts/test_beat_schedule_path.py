"""Tests that `celery beat` has somewhere it is allowed to write."""

from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.app.yml"
DOCKERFILE = PROJECT_ROOT / "Dockerfile"

SCHEDULE_DIRECTORY = "/var/lib/celery"


class TestBeatCanWriteItsSchedule:
    """The default scheduler persists to a file, and needs a writable path.

    `/app` is created by `WORKDIR` and stays root-owned, deliberately, so
    the running user cannot rewrite its own code. Beat's default schedule
    path is the working directory, so the container died on startup with
    `PermissionError: [Errno 13] Permission denied: 'celerybeat-schedule'`
    and no periodic task ever ran. The host-level `test_celery_beat_default
    _starts` missed it because it runs beat in a writable checkout.
    """

    def test_the_image_provides_a_writable_directory(self) -> None:
        """The directory exists and belongs to the user beat runs as."""
        text = DOCKERFILE.read_text()

        assert f"mkdir -p {SCHEDULE_DIRECTORY}" in text
        assert f"chown appuser:appuser {SCHEDULE_DIRECTORY}" in text

    def test_beat_is_pointed_at_it(self) -> None:
        """The command names the path rather than defaulting to the CWD."""
        compose = yaml.safe_load(COMPOSE_FILE.read_text())
        command = compose["services"]["celery-beat"]["command"]

        if "redbeat.RedBeatScheduler" in command:
            # RedBeat keeps the schedule in Redis, there is no file.
            return

        assert "--schedule" in command
        schedule = command[command.index("--schedule") + 1]
        assert schedule.startswith(f"{SCHEDULE_DIRECTORY}/")

    def test_the_schedule_survives_a_restart(self) -> None:
        """A persistent scheduler that loses its file on restart is pointless."""
        compose = yaml.safe_load(COMPOSE_FILE.read_text())
        beat = compose["services"]["celery-beat"]

        if "redbeat.RedBeatScheduler" in beat["command"]:
            return

        mounts = beat.get("volumes") or []
        assert any(mount.endswith(f":{SCHEDULE_DIRECTORY}") for mount in mounts), (
            f"celery-beat has no volume mounted at {SCHEDULE_DIRECTORY}"
        )
