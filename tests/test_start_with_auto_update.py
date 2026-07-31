"""Tests for the Uvicorn auto-update wrapper."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "start_with_auto_update.sh"


def _run_wrapper(tmp_path: Path, env_overrides: dict[str, str], *extra_args: str) -> list[str]:
    argv_path = tmp_path / "argv.txt"
    fake_uvicorn = tmp_path / "fake_uvicorn.py"
    fake_uvicorn.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(argv_path)!r}).write_text('\\n'.join(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "UVICORN_AUTO_UPDATE_ENABLED": "false",
            "UVICORN_AUTO_UPDATE_ATTEMPTS": "1",
        }
    )
    env.update(env_overrides)

    subprocess.run(
        [str(WRAPPER), sys.executable, str(fake_uvicorn), "app.main:app", *extra_args],
        cwd=ROOT,
        env=env,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return argv_path.read_text(encoding="utf-8").splitlines()


def test_wrapper_maps_log_level_to_uvicorn_log_level(tmp_path: Path) -> None:
    """LOG_LEVEL from .env is passed through to Uvicorn's own logger."""

    argv = _run_wrapper(tmp_path, {"LOG_LEVEL": "WARNING  # quiet server logs"})

    assert argv[-2:] == ["--log-level", "warning"]


def test_wrapper_preserves_explicit_uvicorn_log_level(tmp_path: Path) -> None:
    """Explicit Uvicorn CLI log-level settings still take precedence."""

    argv = _run_wrapper(
        tmp_path,
        {"LOG_LEVEL": "WARNING", "UVICORN_LOG_LEVEL": "ERROR"},
        "--log-level",
        "info",
    )

    assert argv.count("--log-level") == 1
    assert argv[-2:] == ["--log-level", "info"]


def test_wrapper_configures_uvicorn_trusted_proxies(tmp_path: Path) -> None:
    """Trusted proxy CIDRs also apply to Uvicorn access-log client addresses."""

    argv = _run_wrapper(
        tmp_path,
        {"TRUSTED_PROXIES": "172.18.0.0/16,10.0.0.8"},
    )

    assert argv[-3:] == [
        "--proxy-headers",
        "--forwarded-allow-ips",
        "172.18.0.0/16,10.0.0.8",
    ]


def test_wrapper_preserves_explicit_forwarded_allow_ips(tmp_path: Path) -> None:
    """An explicit Uvicorn trust boundary takes precedence over the environment."""

    argv = _run_wrapper(
        tmp_path,
        {"TRUSTED_PROXIES": "172.18.0.0/16"},
        "--forwarded-allow-ips=10.0.0.8",
    )

    assert argv.count("--proxy-headers") == 0
    assert argv.count("--forwarded-allow-ips=10.0.0.8") == 1


def test_wrapper_waits_for_child_cleanup_after_sigterm(tmp_path: Path) -> None:
    """A service stop lets Uvicorn finish worker and semaphore cleanup."""

    ready_path = tmp_path / "ready"
    cleanup_path = tmp_path / "cleanup-complete"
    fake_uvicorn = tmp_path / "fake_uvicorn.py"
    fake_uvicorn.write_text(
        "import pathlib, signal, time\n"
        f"ready = pathlib.Path({str(ready_path)!r})\n"
        f"cleanup = pathlib.Path({str(cleanup_path)!r})\n"
        "def shutdown(signum, frame):\n"
        "    time.sleep(0.25)\n"
        "    cleanup.write_text('done', encoding='utf-8')\n"
        "    raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, shutdown)\n"
        "ready.write_text('ready', encoding='utf-8')\n"
        "while True:\n"
        "    time.sleep(1)\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "UVICORN_AUTO_UPDATE_ENABLED": "false",
            "UVICORN_AUTO_UPDATE_ATTEMPTS": "1",
        }
    )
    wrapper = subprocess.Popen(
        [str(WRAPPER), sys.executable, str(fake_uvicorn)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready_path.exists(), "fake Uvicorn process did not start"

        wrapper.send_signal(signal.SIGTERM)
        assert wrapper.poll() is None
        assert not cleanup_path.exists()

        wrapper.wait(timeout=5)
        assert wrapper.returncode == 0
        assert cleanup_path.read_text(encoding="utf-8") == "done"
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait()
