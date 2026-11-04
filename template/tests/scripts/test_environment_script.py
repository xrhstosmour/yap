"""Tests the settings helpers, `env.sh` here and `environment.sh` on the server.

`docker` and `ssh` are stubs that log what they were given, so nothing here
needs a daemon or a server.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"

# Fails every `up` when FAKE_UP_FAIL is set, as a stack that will not start.
DOCKER_STUB = (
    '#!/usr/bin/env bash\necho "$*" >> "$FAKE_LOG"\n'
    '[[ "$*" != *" up "* ]] || [ -z "${FAKE_UP_FAIL:-}" ]\n'
)
# The command goes to one file and standard input to another.
SSH_STUB = (
    "#!/usr/bin/env bash\n"
    'printf \'%s\\n\' "${@: -1}" > "$FAKE_COMMAND"\n'
    'cat > "$FAKE_INPUT"\n'
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project with both scripts and stubs for `docker` and `ssh`."""
    (tmp_path / "scripts").mkdir()
    for name in ("env.sh", "environment.sh", "remote.sh"):
        shutil.copy2(SCRIPTS / name, tmp_path / "scripts" / name)
    (tmp_path / "bin").mkdir()
    for name, body in (("docker", DOCKER_STUB), ("ssh", SSH_STUB)):
        (tmp_path / "bin" / name).write_text(body)
        (tmp_path / "bin" / name).chmod(0o755)
    (tmp_path / ".env").write_text("SECRET_KEY=hunter2\nEMPTY=\nMODE=production\n")
    return tmp_path


def _run(
    project: Path,
    script: str,
    *arguments: str,
    stdin: str = "",
    extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one of the copied scripts inside the project.

    Args:
        project: The project directory holding the scripts.
        script: File name under `scripts/`.
        *arguments: Passed to the script as they are.
        stdin: Standard input for the script.
        extra: More environment variables for the script.

    Returns:
        The finished process.
    """
    environment = {
        "PATH": f"{project / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "COMPOSE": "docker compose",
        "FAKE_LOG": str(project / "docker.log"),
        "FAKE_COMMAND": str(project / "command"),
        "FAKE_INPUT": str(project / "input"),
        **(extra or {}),
    }
    return subprocess.run(
        ["bash", str(project / "scripts" / script), *arguments],
        capture_output=True,
        text=True,
        cwd=project,
        timeout=30,
        env=environment,
        input=stdin,
    )


class TestServerSide:
    """`environment.sh` reads and writes the settings file."""

    def test_list_names_what_is_set_and_never_a_value(self, project: Path) -> None:
        """A listing ends up in terminals and logs."""
        result = _run(project, "environment.sh", "list")
        assert result.stdout.split() == [
            "SECRET_KEY=<set>",
            "EMPTY=<empty>",
            "MODE=<set>",
        ]
        assert "hunter2" not in result.stdout

    def test_get_prints_one_value(self, project: Path) -> None:
        """Only the value, so it can be used in a pipeline."""
        result = _run(project, "environment.sh", "get", "MODE")
        assert result.stdout == "production\n"

    def test_set_replaces_a_value_and_brings_the_stack_up(self, project: Path) -> None:
        """The services read the file only when they are created."""
        result = _run(project, "environment.sh", "set", "MODE", stdin="staging\n")
        assert result.returncode == 0, result.stderr
        text = (project / ".env").read_text()
        assert text.count("MODE=") == 1
        assert "MODE=staging" in text
        assert "SECRET_KEY=hunter2" in text
        assert (project / ".env").stat().st_mode & 0o777 == 0o600
        assert "up -d --no-build" in (project / "docker.log").read_text()

    def test_get_of_a_setting_that_is_not_set_fails(self, project: Path) -> None:
        """An empty answer would be taken for an empty value."""
        result = _run(project, "environment.sh", "get", "NOT_SET")
        assert result.returncode == 1
        assert "NOT_SET is not set" in result.stderr

    def test_a_value_the_stack_will_not_start_on_is_taken_back(
        self, project: Path
    ) -> None:
        """Otherwise the next container recreated for any reason fails on it."""
        before = (project / ".env").read_text()
        result = _run(
            project,
            "environment.sh",
            "set",
            "MODE",
            stdin="broken\n",
            extra={"FAKE_UP_FAIL": "1"},
        )
        assert result.returncode == 1
        assert "previous settings are back" in result.stderr
        assert (project / ".env").read_text() == before

    def test_set_adds_a_missing_value(self, project: Path) -> None:
        """A new setting is appended."""
        _run(project, "environment.sh", "set", "NEW_KEY", stdin="a=b\n")
        assert "NEW_KEY=a=b" in (project / ".env").read_text()

    @pytest.mark.parametrize("key", ["lower", "A;B", "1A"])
    def test_a_name_that_is_not_a_setting_is_refused(
        self, project: Path, key: str
    ) -> None:
        """The name is matched against the file as a pattern."""
        result = _run(project, "environment.sh", "get", key)
        assert result.returncode == 1
        assert "is not a setting name" in result.stderr


class TestClientSide:
    """`env.sh` checks its input and keeps the value out of the command line."""

    def test_the_script_is_executable(self) -> None:
        """Copier does not preserve a mode the template never had."""
        assert (SCRIPTS / "env.sh").stat().st_mode & 0o111

    def test_a_missing_action_shows_the_usage(self, project: Path) -> None:
        """The server and the action are required."""
        result = _run(project, "env.sh", "deploy@server.example")
        assert result.returncode == 2
        assert "Usage" in result.stderr

    def test_a_name_that_is_not_a_setting_is_refused(self, project: Path) -> None:
        """The name ends up in a command line on the server."""
        result = _run(project, "env.sh", "deploy@server.example", "get", "a;b")
        assert result.returncode == 1
        assert "is not a setting name" in result.stderr

    def test_the_value_goes_on_standard_input_not_the_command(
        self, project: Path
    ) -> None:
        """Arguments of a running process are readable by other users."""
        result = _run(
            project,
            "env.sh",
            "deploy@server.example",
            "set",
            "API_TOKEN",
            stdin="s3cret\n",
        )
        assert result.returncode == 0, result.stderr
        assert (project / "input").read_text() == "s3cret\n"
        command = (project / "command").read_text()
        assert "set API_TOKEN" in command
        assert "s3cret" not in command

    def test_a_value_as_an_argument_is_refused(self, project: Path) -> None:
        """It would stay in the shell history and the process list."""
        result = _run(
            project, "env.sh", "deploy@server.example", "set", "API_TOKEN", "s3cret"
        )
        assert result.returncode == 2
        assert not (project / "command").exists()

    def test_an_empty_value_is_refused(self, project: Path) -> None:
        """A forgotten pipe must not blank a setting on the server."""
        result = _run(project, "env.sh", "deploy@server.example", "set", "API_TOKEN")
        assert result.returncode == 1
        assert "No value for API_TOKEN" in result.stderr
        assert not (project / "command").exists()

    def test_the_local_settings_follow_so_a_deploy_keeps_the_value(
        self, project: Path
    ) -> None:
        """Deploys write the server's file again from `.env.production`."""
        local = project / ".env.production"
        local.write_text("API_TOKEN=old\nMODE=production\n")
        local.chmod(0o600)
        result = _run(
            project,
            "env.sh",
            "deploy@server.example",
            "set",
            "API_TOKEN",
            stdin="new\n",
        )
        assert result.returncode == 0, result.stderr
        text = local.read_text()
        assert "API_TOKEN=new" in text
        assert "API_TOKEN=old" not in text
        assert "MODE=production" in text
        assert local.stat().st_mode & 0o777 == 0o600

    def test_missing_local_settings_are_reported_not_created(
        self, project: Path
    ) -> None:
        """The operator is told the next deploy would undo the value."""
        result = _run(
            project,
            "env.sh",
            "deploy@server.example",
            "set",
            "API_TOKEN",
            stdin="new\n",
        )
        assert result.returncode == 0, result.stderr
        assert "No .env.production here" in result.stdout
        assert not (project / ".env.production").exists()

    def test_the_github_secret_is_mentioned(self, project: Path) -> None:
        """A deploy from GitHub writes the settings from a secret, not a file."""
        result = _run(
            project,
            "env.sh",
            "deploy@server.example",
            "set",
            "API_TOKEN",
            stdin="new\n",
        )
        assert "ENV_PRODUCTION" in result.stdout
