"""A multi-worker service is ready only once every Uvicorn worker has started."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import textwrap
import time
import urllib.request
from pathlib import Path

import pytest

from app.services import worker_readiness

REPO_ROOT = Path(__file__).resolve().parents[1]
UVICORN_ARGV = ["/venv/bin/python", "-m", "uvicorn", "app.main:app", "--port", "8001", "--workers", "2"]


@pytest.fixture
def service(monkeypatch, tmp_path):
    """This test process plays one worker of a two-worker Uvicorn service."""
    for name, value in (("_resolved", False), ("_directory", None), ("_expected", None), ("_service_ready", False)):
        monkeypatch.setattr(worker_readiness, name, value)
    monkeypatch.setattr(worker_readiness, "_base_directory", lambda: tmp_path)
    monkeypatch.setattr(worker_readiness, "_parent_details", lambda: (4321, UVICORN_ARGV, "98765"))
    return tmp_path / "myportal-workers-4321-98765"


def _exited_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", ""])
    process.wait()
    return process.pid


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (UVICORN_ARGV, 2),
        (["python", "-m", "uvicorn", "app.main:app", "--workers=4"], 4),
        (["python", "-m", "uvicorn", "app.main:app", "--workers", "1"], None),
        (["python", "-m", "uvicorn", "app.main:app", "--reload"], None),
        (["python", "-m", "uvicorn", "app.main:app", "--workers", "many"], None),
        (["python", "-m", "uvicorn", "app.main:app", "--workers"], None),
        (["gunicorn", "--workers", "4", "app.main:app"], None),
    ],
)
def test_configured_workers(argv, expected):
    assert worker_readiness.configured_workers(argv) == expected


def test_single_process_server_is_not_gated():
    # The test runner is not a Uvicorn worker, so the check does not apply.
    assert worker_readiness._parent_details() is None


def test_waits_for_every_worker(service):
    worker_readiness.mark_started()
    assert (service / str(os.getpid())).is_file()
    assert worker_readiness.status() == "pending: 1 of 2 workers started"

    # The other worker (any running process will do) finishes its startup.
    (service / str(os.getppid())).touch()
    assert worker_readiness.status() == "ok"
    assert (service / "ready").is_file()


def test_the_last_worker_to_start_records_the_service_ready(service):
    service.mkdir(mode=0o700)
    (service / str(os.getppid())).touch()
    worker_readiness.mark_started()
    assert (service / "ready").is_file()


def test_readiness_is_kept_when_a_worker_restarts_later(service, monkeypatch):
    worker_readiness.mark_started()
    (service / str(os.getppid())).touch()
    assert worker_readiness.status() == "ok"

    (service / str(os.getppid())).unlink()
    assert worker_readiness.status() == "ok"
    # Other workers of the same service see the recorded state too.
    monkeypatch.setattr(worker_readiness, "_service_ready", False)
    assert worker_readiness.status() == "ok"


def test_workers_that_exited_do_not_count(service):
    worker_readiness.mark_started()
    (service / str(_exited_pid())).touch()
    assert worker_readiness.status() == "pending: 1 of 2 workers started"


def test_a_stuck_worker_stops_holding_the_service_back(service):
    worker_readiness.mark_started()
    long_ago = time.time() - worker_readiness.GRACE_SECONDS - 1
    os.utime(service / str(os.getpid()), (long_ago, long_ago))
    assert worker_readiness.status() == "ok"


def test_markers_of_an_earlier_run_never_count(service, tmp_path):
    earlier = tmp_path / "myportal-workers-4321-11111"
    earlier.mkdir(mode=0o700)
    (earlier / "ready").touch()
    (earlier / str(os.getppid())).touch()
    worker_readiness.mark_started()
    assert worker_readiness.status() == "pending: 1 of 2 workers started"


def test_an_unusable_directory_does_not_gate(service, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    service.symlink_to(other)
    worker_readiness.mark_started()
    assert worker_readiness.status() is None
    assert not list(other.iterdir())


def test_a_shared_directory_does_not_gate(service):
    service.mkdir(mode=0o777)
    service.chmod(0o777)
    assert worker_readiness.status() is None


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="needs /proc")
def test_uvicorn_service_is_ready_only_after_every_worker_starts(tmp_path):
    # The first worker to start serves immediately; the second takes a while.
    (tmp_path / "probe_app.py").write_text(
        textwrap.dedent(
            f"""
            import asyncio, json, os
            from app.services import worker_readiness

            async def app(scope, receive, send):
                if scope["type"] == "lifespan":
                    while True:
                        message = await receive()
                        if message["type"] == "lifespan.startup":
                            try:
                                os.close(os.open({str(tmp_path / "first")!r}, os.O_CREAT | os.O_EXCL))
                            except FileExistsError:
                                while not os.path.exists({str(tmp_path / "release")!r}):
                                    await asyncio.sleep(0.05)
                            worker_readiness.mark_started()
                            await send({{"type": "lifespan.startup.complete"}})
                        else:
                            await send({{"type": "lifespan.shutdown.complete"}})
                            return
                body = json.dumps({{"workers": worker_readiness.status()}}).encode()
                await send({{"type": "http.response.start", "status": 200, "headers": []}})
                await send({{"type": "http.response.body", "body": body}})
            """
        ),
        encoding="utf-8",
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    environment = {**os.environ, "PYTHONPATH": f"{tmp_path}{os.pathsep}{REPO_ROOT}", "TMPDIR": str(tmp_path)}
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "probe_app:app", "--port", str(port), "--workers", "2",
         "--log-level", "warning"],
        cwd=tmp_path,
        env=environment,
    )

    def workers() -> str | None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                    f"http://127.0.0.1:{port}/", timeout=5
                ) as response:
                    return json.loads(response.read())["workers"]
            except OSError:
                time.sleep(0.1)
        raise AssertionError("the service did not answer")

    try:
        assert workers() == "pending: 1 of 2 workers started"
        (tmp_path / "release").touch()
        deadline = time.monotonic() + 30
        while (status := workers()) != "ok" and time.monotonic() < deadline:
            time.sleep(0.1)
        assert status == "ok"
    finally:
        server.terminate()
        server.wait(timeout=30)
