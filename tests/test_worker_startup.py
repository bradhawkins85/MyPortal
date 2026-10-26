"""Exercise launcher shell expansion without starting application workers."""

import os
import shlex
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def runtime(tmp_path):
    release = tmp_path / "release"
    python = release / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    arguments = tmp_path / "arguments"
    python.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >"$ARGUMENTS_FILE"\n')
    python.chmod(0o755)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "python").symlink_to(python)
    readlink = binaries / "readlink"
    readlink.write_text('#!/bin/sh\nprintf "%s\\n" "$TEST_RELEASE"\n')
    readlink.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "TEST_RELEASE": str(release),
        "ARGUMENTS_FILE": str(arguments),
        "MYPORTAL_BIND_HOST": "127.0.0.1",
        "PORT": "8000",
        "TRUSTED_PROXIES": "",
    }
    environment.pop("WEB_CONCURRENCY", None)
    environment.pop("UVICORN_TIMEOUT_KEEP_ALIVE", None)
    return environment, arguments


@pytest.mark.parametrize("slot, port", [("blue", "8001"), ("green", "8002")])
@pytest.mark.parametrize("configured, expected", [(None, "4"), ("", "4"), ("2", "2"), ("6", "6")])
def test_systemd_launches_default_or_configured_workers(runtime, slot, port, configured, expected):
    environment, arguments = runtime
    if configured is not None:
        environment["WEB_CONCURRENCY"] = configured
    unit = (ROOT / "deploy/systemd/myportal@.service").read_text()
    command = next(line.removeprefix("ExecStart=") for line in unit.splitlines() if line.startswith("ExecStart="))
    # Apply systemd's literal-dollar escaping and instance expansion, then run
    # the unit's real shell with only readlink and the Python executable faked.
    argv = [argument.replace("$$", "$").replace("%i", slot) for argument in shlex.split(command)]
    subprocess.run(argv, env=environment, capture_output=True, text=True, check=True, timeout=5)
    assert arguments.read_text().splitlines() == [
        "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
        "--port", port, "--workers", expected, "--timeout-keep-alive", "75",
    ]


@pytest.mark.parametrize("configured, expected", [(None, "4"), ("", "4"), ("2", "2"), ("6", "6")])
def test_docker_launches_default_or_configured_workers(runtime, configured, expected):
    environment, arguments = runtime
    if configured is not None:
        environment["WEB_CONCURRENCY"] = configured
    source = (ROOT / "docker/entrypoint.sh").read_text()
    # Run the actual serve branch while replacing database preparation only.
    branch = source[source.index('case "${1:-serve}" in'):]
    program = "set -euo pipefail\nVERSION=test\nwait_for_database() { :; }\nrun_migrations() { :; }\n" + branch
    subprocess.run(["bash", "-c", program], env=environment, capture_output=True, text=True, check=True, timeout=5)
    assert arguments.read_text().splitlines() == [
        "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0",
        "--port", "8000", "--workers", expected, "--timeout-keep-alive", "75",
    ]


def test_uvicorn_keep_alive_is_configurable(runtime):
    environment, arguments = runtime
    environment["UVICORN_TIMEOUT_KEEP_ALIVE"] = "120"
    unit = (ROOT / "deploy/systemd/myportal@.service").read_text()
    command = next(line.removeprefix("ExecStart=") for line in unit.splitlines() if line.startswith("ExecStart="))
    argv = [argument.replace("$$", "$").replace("%i", "blue") for argument in shlex.split(command)]
    subprocess.run(argv, env=environment, capture_output=True, text=True, check=True, timeout=5)
    assert arguments.read_text().splitlines()[-2:] == ["--timeout-keep-alive", "120"]


def test_nginx_closes_idle_upstream_connections_before_uvicorn():
    # Uvicorn's own 5 second default closes connections nginx still holds; a
    # request sent on one fails, and the blue/green include has no live
    # server to retry on, so nginx serves its upgrade page.
    for config in (
        ROOT / "deploy/nginx/myportal-bluegreen.conf",
        ROOT / "deploy/nginx/myportal.conf",
        ROOT / "scripts/myportal-docker.sh",
    ):
        assert "keepalive_timeout 60s;" in config.read_text(), config
