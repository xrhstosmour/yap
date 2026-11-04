"""Tests the release bookkeeping `deploy.sh` and `rollback.sh` run on the server.

`docker` is a stub that keeps image tags as files and logs its arguments, so
what is checked is which images get tagged, removed and started, never a real
daemon.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "releases.sh"

# A tag is a file holding the image it points at, so `tag` copies it.
STUB = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG"
case "$*" in
    *"config --images"*)
        printf '%s\\n' "${FAKE_IMAGES:-demo-app
demo-worker:latest
postgres:16}" ;;
    "tag "*) cp "$FAKE_STATE/$2" "$FAKE_STATE/$3" ;;
    "rmi "*) rm -f "$FAKE_STATE/$2" ;;
    "image inspect --format "*) cat "$FAKE_STATE/${@: -1}" ;;
    "image inspect "*) [ -f "$FAKE_STATE/$3" ] ;;
esac
"""


@pytest.fixture
def server(tmp_path: Path) -> Path:
    """A project directory named `demo`, with the script and a stub `docker`."""
    project = tmp_path / "demo"
    (project / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, project / "scripts" / "releases.sh")
    (tmp_path / "bin").mkdir()
    (tmp_path / "state").mkdir()
    docker = tmp_path / "bin" / "docker"
    docker.write_text(STUB)
    docker.chmod(0o755)
    return project


def _build(server: Path, build: str) -> None:
    """Make `latest` point at a new build of both images, as `compose build` does."""
    for image in ("demo-app", "demo-worker"):
        (server.parent / "state" / f"{image}:latest").write_text(f"{image}-{build}\n")


def _reference(server: Path, image: str) -> str:
    """The image a tag points at, empty when the tag does not exist."""
    path = server.parent / "state" / image
    return path.read_text().strip() if path.exists() else ""


def _releases(
    server: Path, *arguments: str, images: str = ""
) -> subprocess.CompletedProcess[str]:
    """Run the script inside the project directory.

    Args:
        server: The project directory.
        *arguments: Passed to the script as they are.
        images: What `compose config --images` prints, the default when empty.

    Returns:
        The finished process.
    """
    environment = {
        "PATH": f"{server.parent / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "COMPOSE": "docker compose -f docker-compose.yml",
        "FAKE_LOG": str(server.parent / "docker.log"),
        "FAKE_STATE": str(server.parent / "state"),
        "RELEASES_KEPT": "3",
    }
    if images:
        environment["FAKE_IMAGES"] = images
    return subprocess.run(
        ["bash", str(server / "scripts" / "releases.sh"), *arguments],
        capture_output=True,
        text=True,
        cwd=server,
        timeout=30,
        env=environment,
    )


def _deploy(server: Path, release: str) -> None:
    """Build and record a release, as a deploy that came up healthy does."""
    _build(server, release)
    assert _releases(server, "record", release).returncode == 0


def _calls(server: Path) -> list[str]:
    """The arguments the stub `docker` was called with, one string a call."""
    log = server.parent / "docker.log"
    return log.read_text().splitlines() if log.exists() else []


def _listed(server: Path) -> list[str]:
    """The recorded releases, oldest first."""
    return (server / ".releases").read_text().split()


class TestRecord:
    """A deployed release keeps its images under its own tag."""

    def test_only_the_project_images_are_tagged(self, server: Path) -> None:
        """The vendored infrastructure images are not ours to keep."""
        _deploy(server, "abc1234")
        tags = [call for call in _calls(server) if call.startswith("tag ")]
        assert tags == [
            "tag demo-app:latest demo-app:abc1234",
            "tag demo-worker:latest demo-worker:abc1234",
        ]

    def test_the_oldest_release_is_forgotten_past_the_limit(self, server: Path) -> None:
        """Disk is bounded, a rollback only goes back so far."""
        for release in ("a1", "b2", "c3", "d4"):
            _deploy(server, release)
        assert _listed(server) == ["b2", "c3", "d4"]
        assert _reference(server, "demo-app:a1") == ""
        assert _reference(server, "demo-worker:a1") == ""
        assert _reference(server, "demo-app:b2") != ""

    def test_deploying_a_release_again_does_not_list_it_twice(
        self, server: Path
    ) -> None:
        """A repeated release moves to the end, so it is never removed as old."""
        for release in ("a1", "b2", "a1"):
            _deploy(server, release)
        assert _listed(server) == ["b2", "a1"]

    def test_a_release_with_a_shell_character_is_refused(self, server: Path) -> None:
        """The value reaches the server inside a command line."""
        result = _releases(server, "record", "abc;touch x")
        assert result.returncode == 1
        assert "is not a release" in result.stderr

    def test_a_project_without_images_is_not_recorded(self, server: Path) -> None:
        """Compose names the images after its project, which may not be the directory.

        Matching nothing used to record the release anyway, and a rollback to
        it then reported success while changing nothing.
        """
        _build(server, "a1")
        result = _releases(server, "record", "a1", images="postgres:16")
        assert result.returncode == 1
        assert "No image of demo found" in result.stderr
        assert not (server / ".releases").exists()


class TestRestore:
    """A rollback runs the images of an earlier release."""

    @pytest.fixture(autouse=True)
    def recorded(self, server: Path) -> None:
        """Three releases deployed, the last one running."""
        for release in ("a1", "b2", "c3"):
            _deploy(server, release)

    def test_the_previous_release_is_the_default(self, server: Path) -> None:
        """It becomes `latest` and is started without a build."""
        result = _releases(server, "restore")
        assert result.returncode == 0, result.stderr
        assert _reference(server, "demo-app:latest") == "demo-app-b2"
        assert any("up -d --no-build" in call for call in _calls(server))

    def test_the_running_release_follows_the_images_not_the_list(
        self, server: Path
    ) -> None:
        """After one rollback the next default goes one further back."""
        _releases(server, "restore")
        assert _releases(server, "restore").returncode == 0
        assert _reference(server, "demo-app:latest") == "demo-app-a1"

    def test_a_release_rolled_back_from_can_be_restored_again(
        self, server: Path
    ) -> None:
        """A rollback that was a mistake is undone by naming the newer release."""
        _releases(server, "restore", "a1")
        assert _releases(server, "restore", "c3").returncode == 0
        assert _reference(server, "demo-app:latest") == "demo-app-c3"

    def test_a_failed_deploy_goes_back_to_the_last_healthy_release(
        self, server: Path
    ) -> None:
        """The broken build is under `latest` and was never recorded."""
        _build(server, "d4")
        assert _releases(server, "restore").returncode == 0
        assert _reference(server, "demo-app:latest") == "demo-app-c3"

    def test_the_running_release_is_not_restored(self, server: Path) -> None:
        """There is nothing to do."""
        result = _releases(server, "restore", "c3")
        assert result.returncode == 1
        assert "is the release that is running" in result.stderr

    def test_the_oldest_release_has_nothing_older(self, server: Path) -> None:
        """The default stops at the start of the list."""
        _releases(server, "restore", "a1")
        result = _releases(server, "restore")
        assert result.returncode == 1
        assert "is the release that is running" in result.stderr

    def test_an_unknown_release_names_the_recorded_ones(self, server: Path) -> None:
        """A typo gets the choices, not a half started rollback."""
        result = _releases(server, "restore", "zz9")
        assert result.returncode == 1
        assert "a1 b2 c3" in result.stderr
        assert not any("up -d" in call for call in _calls(server))

    def test_a_missing_image_stops_before_anything_is_retagged(
        self, server: Path
    ) -> None:
        """The running stack is left as it was."""
        (server.parent / "state" / "demo-worker:b2").unlink()
        result = _releases(server, "restore")
        assert result.returncode == 1
        assert "demo-worker:b2 is gone" in result.stderr
        assert _reference(server, "demo-app:latest") == "demo-app-c3"

    def test_a_project_without_images_restores_nothing(self, server: Path) -> None:
        """It must not report a rollback that changed nothing."""
        result = _releases(server, "restore", images="postgres:16")
        assert result.returncode == 1
        assert "Restored" not in result.stdout
        assert not any("up -d" in call for call in _calls(server))
