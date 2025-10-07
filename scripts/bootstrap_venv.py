#!/usr/bin/env python3
"""Create a local virtual environment and install MyPortal in editable mode."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import venv
from pathlib import Path


def _venv_python(venv_path: Path) -> Path:
    if os.name == "nt":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def ensure_virtualenv(venv_path: Path, recreate: bool = False) -> Path:
    if recreate and venv_path.exists():
        shutil.rmtree(venv_path)

    if not venv_path.exists():
        builder = venv.EnvBuilder(with_pip=True)
        builder.create(venv_path)

    python_executable = _venv_python(venv_path)
    if not python_executable.exists():
        raise RuntimeError("Virtual environment python executable was not created correctly.")

    return python_executable


def install_editable(project_root: Path, python_executable: Path) -> None:
    env = os.environ.copy()
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")

    subprocess.check_call(
        [str(python_executable), "-m", "pip", "install", "--upgrade", "pip"],
        cwd=str(project_root),
        env=env,
    )
    subprocess.check_call(
        [str(python_executable), "-m", "pip", "install", "-e", str(project_root)],
        cwd=str(project_root),
        env=env,
    )



def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Bootstrap a dedicated virtual environment so that `pip install -e .` "
            "works even on systems with externally managed Python installations."
        )
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Recreate the .venv directory instead of reusing the existing environment.",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    venv_path = project_root / ".venv"

    python_executable = ensure_virtualenv(venv_path, recreate=args.recreate)
    install_editable(project_root, python_executable)

    if os.name == "nt":
        activation_command = r".\\.venv\\Scripts\\activate"
    else:
        activation_command = "source .venv/bin/activate"

    print(
        "MyPortal has been installed in editable mode inside .venv.\n"
        f"Run `{activation_command}` to activate the environment before development sessions."
    )


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc
