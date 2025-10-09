#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
VENV_DIR="${PROJECT_ROOT}/.venv"

detect_python_interpreter() {
  local interpreter=""
  if [[ -x "${VENV_DIR}/bin/python" ]]; then
    interpreter="${VENV_DIR}/bin/python"
  elif [[ -x "${VENV_DIR}/Scripts/python.exe" ]]; then
    interpreter="${VENV_DIR}/Scripts/python.exe"
  elif command -v python3 >/dev/null 2>&1; then
    interpreter=$(command -v python3)
  elif command -v python >/dev/null 2>&1; then
    interpreter=$(command -v python)
  fi
  printf '%s' "$interpreter"
}

PYTHON_INTERPRETER=$(detect_python_interpreter)

cd "$PROJECT_ROOT"

# Load GitHub credentials from .env in a safe manner
if [ -f .env ]; then
  if [[ -z "$PYTHON_INTERPRETER" ]]; then
    echo "Warning: Unable to locate a python interpreter to parse .env credentials. Skipping GitHub authentication." >&2
  else
    while IFS=':' read -r key encoded || [ -n "$key" ]; do
      if [ -z "${key:-}" ]; then
        continue
      fi
      value=$(printf '%s' "$encoded" | base64 --decode)
      case "$key" in
        GITHUB_USERNAME) GITHUB_USERNAME="$value" ;;
        GITHUB_PASSWORD) GITHUB_PASSWORD="$value" ;;
      esac
    done < <(
      "$PYTHON_INTERPRETER" - <<'PY'
import base64
from pathlib import Path

env_path = Path('.env')
if env_path.exists():
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if key not in {'GITHUB_USERNAME', 'GITHUB_PASSWORD'}:
            continue
        value = value.strip()
        if value and len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        encoded = base64.b64encode(value.encode()).decode()
        print(f"{key}:{encoded}")
PY
    )
  fi
fi

REMOTE_URL=$(git config --get remote.origin.url || true)

PRE_PULL_HEAD=$(git rev-parse HEAD)

if [[ -n "${GITHUB_USERNAME:-}" && -n "${GITHUB_PASSWORD:-}" && "$REMOTE_URL" == https://* ]]; then
  AUTH_REMOTE_URL="https://${GITHUB_USERNAME}:${GITHUB_PASSWORD}@${REMOTE_URL#https://}"
  git pull "$AUTH_REMOTE_URL" main
else
  git pull origin main
fi

POST_PULL_HEAD=$(git rev-parse HEAD)

if [[ "$PRE_PULL_HEAD" != "$POST_PULL_HEAD" ]]; then
  echo "Repository updated to $POST_PULL_HEAD. Installing dependencies and restarting service."

  if [[ -n "$PYTHON_INTERPRETER" && "$PYTHON_INTERPRETER" == "${VENV_DIR}/bin/python" ]]; then
    PYTHON_BIN="${PYTHON_INTERPRETER}"
  elif [[ -n "$PYTHON_INTERPRETER" && "$PYTHON_INTERPRETER" == "${VENV_DIR}/Scripts/python.exe" ]]; then
    PYTHON_BIN="${PYTHON_INTERPRETER}"
  elif [[ -x "${VENV_DIR}/bin/python" ]]; then
    PYTHON_BIN="${VENV_DIR}/bin/python"
  elif [[ -x "${VENV_DIR}/Scripts/python.exe" ]]; then
    PYTHON_BIN="${VENV_DIR}/Scripts/python.exe"
  else
    PYTHON_BIN=""
  fi

  cleanup_invalid_distribution() {
    local interpreter="$1"
    if [[ -z "$interpreter" ]]; then
      return
    fi
    "$interpreter" - <<'PY'
from __future__ import annotations

import shutil
import site
import sys
from pathlib import Path

PROJECT_NAME = "myportal"
PREFIX = f"~{PROJECT_NAME}"

def iter_site_packages() -> set[Path]:
    paths: set[Path] = set()
    for getter in (getattr(site, "getsitepackages", None), getattr(site, "getusersitepackages", None)):
        if getter is None:
            continue
        try:
            value = getter()
        except (AttributeError, TypeError):
            continue
        if isinstance(value, str):
            value = [value]
        for path in value:
            if path:
                paths.add(Path(path))

    py_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    paths.add(Path(sys.prefix) / "lib" / py_version / "site-packages")
    if sys.platform.startswith("win"):
        paths.add(Path(sys.prefix) / "Lib" / "site-packages")

    valid_paths: set[Path] = set()
    for path in paths:
        try:
            if path.exists():
                valid_paths.add(path)
        except PermissionError:
            print(f"Skipping inaccessible site-packages directory: {path}", file=sys.stderr)
    return valid_paths


for site_path in iter_site_packages():
    try:
        entries = list(site_path.iterdir())
    except PermissionError as exc:
        print(f"Skipping inaccessible site-packages directory {site_path}: {exc}", file=sys.stderr)
        continue
    for entry in entries:
        name = entry.name
        if not name.lower().startswith(PREFIX):
            continue
        try:
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            print(f"Removed stale distribution entry: {entry}")
        except Exception as exc:  # noqa: BLE001
            print(f"Failed to remove stale distribution entry {entry}: {exc}", file=sys.stderr)
PY
  }

  if [[ -n "$PYTHON_BIN" ]]; then
    echo "Using virtual environment interpreter at ${PYTHON_BIN}" >&2
    cleanup_invalid_distribution "$PYTHON_BIN"
    "$PYTHON_BIN" -m pip install -e "$PROJECT_ROOT"
  else
    echo "Warning: .venv not found, falling back to system python" >&2
    if [[ -n "$PYTHON_INTERPRETER" ]]; then
      PYTHON_FALLBACK="$PYTHON_INTERPRETER"
    else
      PYTHON_FALLBACK=$(detect_python_interpreter)
    fi
    if [[ -z "$PYTHON_FALLBACK" ]]; then
      echo "Error: Unable to locate a python interpreter for dependency installation." >&2
      exit 1
    fi
    cleanup_invalid_distribution "$PYTHON_FALLBACK"
    "$PYTHON_FALLBACK" -m pip install -e "$PROJECT_ROOT"
  fi

  systemctl restart myportal.service
else
  echo "No changes detected from remote. Skipping dependency installation and service restart."
fi
