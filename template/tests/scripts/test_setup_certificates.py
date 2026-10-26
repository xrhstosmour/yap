"""Tests what `setup.sh` does when the broker's CA is a directory.

A compose run before the certificate exists makes `Docker` create a
directory at the bind mount's source. Generating over it would fail later and
less clearly, so `setup.sh` has to stop and say what happened.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SETUP = Path(__file__).resolve().parents[2] / "scripts" / "setup.sh"
EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
CA = Path("containers/distribute/brokers/rabbitmq/certificates/ca_certificate.pem")


class TestCaIsADirectory:
    """A directory where the CA belongs is reported, not worked around."""

    def test_setup_stops_with_the_path_and_the_fix(self, tmp_path: Path) -> None:
        """It exits non-zero before installing anything or touching the path."""
        (tmp_path / "scripts").mkdir()
        shutil.copy(SETUP, tmp_path / "scripts" / "setup.sh")
        shutil.copy(EXAMPLE, tmp_path / ".env.example")
        (tmp_path / CA).mkdir(parents=True)
        # A `uv` on the `PATH` keeps the script from fetching the installer.
        binary = tmp_path / "bin"
        binary.mkdir()
        uv = binary / "uv"
        uv.write_text("#!/bin/sh\nexit 0\n")
        uv.chmod(0o755)

        result = subprocess.run(
            ["bash", str(tmp_path / "scripts" / "setup.sh")],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env={
                **os.environ,
                "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}",
                "YAP_SYNC": "1",
            },
        )

        assert result.returncode != 0
        assert f"{tmp_path / CA} is a directory" in result.stdout
        assert "Remove it and run setup.sh again" in result.stdout
        assert (tmp_path / CA).is_dir()
