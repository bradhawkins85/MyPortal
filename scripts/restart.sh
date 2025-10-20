#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
VENV_DIR="${PROJECT_ROOT}/.venv"

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

select_python() {
  if [[ -x "${VENV_DIR}/bin/python" ]]; then
    printf '%s' "${VENV_DIR}/bin/python"
    return
  fi
  if [[ -x "${VENV_DIR}/Scripts/python.exe" ]]; then
    printf '%s' "${VENV_DIR}/Scripts/python.exe"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$(command -v python3)"
    return
  fi
  if command -v python >/dev/null 2>&1; then
    printf '%s' "$(command -v python)"
    return
  fi
  printf ''
}

PYTHON_BIN=$(select_python)

if [[ -z "$PYTHON_BIN" ]]; then
  echo "Error: Unable to locate a python interpreter for dependency installation." >&2
  exit 1
fi

echo "Using python interpreter at ${PYTHON_BIN}" >&2
cleanup_invalid_distribution "$PYTHON_BIN"
"$PYTHON_BIN" -m pip install -e "$PROJECT_ROOT"

restart_service() {
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not available on this host; skipping service restart." >&2
    return
  fi

  local service_name="myportal.service"
  local attempts=()

  if systemctl restart "$service_name" >/dev/null 2>&1; then
    echo "Restarted ${service_name} via systemctl." >&2
    return
  else
    local exit_code=$?
    attempts+=("systemctl restart ${service_name} (exit ${exit_code})")
  fi

  if [[ "${EUID:-$(id -u)}" != "0" ]] && command -v sudo >/dev/null 2>&1; then
    if sudo -n systemctl restart "$service_name" >/dev/null 2>&1; then
      echo "Restarted ${service_name} via sudo systemctl." >&2
      return
    else
      local exit_code=$?
      attempts+=("sudo -n systemctl restart ${service_name} (exit ${exit_code})")
    fi
  fi

  if systemctl --user restart "$service_name" >/dev/null 2>&1; then
    echo "Restarted ${service_name} via systemctl --user." >&2
    return
  else
    local exit_code=$?
    attempts+=("systemctl --user restart ${service_name} (exit ${exit_code})")
  fi

  echo "Warning: Unable to restart ${service_name} automatically." >&2
  if ((${#attempts[@]} > 0)); then
    printf '  Tried: %s\n' "${attempts[@]}" >&2
  fi
  echo "Run 'sudo systemctl restart ${service_name}' or review service permissions and logs." >&2
}

restart_service
