"""Tests for the Uvicorn auto-update wrapper."""

from __future__ import annotations

import os
import subprocess
import sys
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
