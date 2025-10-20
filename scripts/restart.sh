#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
VENV_DIR="${PROJECT_ROOT}/.venv"

read_env_var() {
  local key="$1"
  local default_value="${2:-}"

  if [[ -n "${!key:-}" ]]; then
    printf '%s' "${!key}"
    return
  fi

  if [[ -z "$PYTHON_BIN" || ! -f "${PROJECT_ROOT}/.env" ]]; then
    printf '%s' "$default_value"
    return
  fi

  local value
  value=$(ENV_LOOKUP_KEY="$key" ENV_LOOKUP_DEFAULT="$default_value" PROJECT_ROOT="$PROJECT_ROOT" "$PYTHON_BIN" - <<'PY'
import os
from pathlib import Path

key = os.environ["ENV_LOOKUP_KEY"]
default = os.environ.get("ENV_LOOKUP_DEFAULT", "")
env_path = Path(Path(os.environ["PROJECT_ROOT"]) / ".env")

if not env_path.exists():
    print(default)
    raise SystemExit

for raw_line in env_path.read_text().splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    name, value = line.split("=", 1)
    if name.strip() != key:
        continue
    value = value.strip()
    if value and len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    print(value)
    break
else:
    print(default)
PY
  )

  printf '%s' "$value"
}

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
  local custom_command
  custom_command=$(read_env_var "APP_RESTART_COMMAND")

  local service_name
  service_name=$(read_env_var "SYSTEMD_SERVICE_NAME" "myportal")
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not available on this host; skipping service restart." >&2
    return 0
  fi

  if [[ -n "$service_name" && "${service_name}" != *.service ]]; then
    service_name+=".service"
  fi

  local attempts=()
  local succeeded=0

  if [[ -n "$custom_command" ]]; then
    if bash -lc "$custom_command" >/dev/null 2>&1; then
      echo "Restarted service via custom command." >&2
      succeeded=1
    else
      local exit_code=$?
      attempts+=("custom command '${custom_command}' (exit ${exit_code})")
    fi
  fi

  if (( ! succeeded )); then
    if systemctl restart "$service_name" >/dev/null 2>&1; then
      echo "Restarted ${service_name} via systemctl." >&2
      succeeded=1
    else
      local exit_code=$?
      attempts+=("systemctl restart ${service_name} (exit ${exit_code})")
    fi
  fi

  if (( ! succeeded )) && [[ "${EUID:-$(id -u)}" != "0" ]] && command -v sudo >/dev/null 2>&1; then
    if sudo -n systemctl restart "$service_name" >/dev/null 2>&1; then
      echo "Restarted ${service_name} via sudo systemctl." >&2
      succeeded=1
    else
      local exit_code=$?
      attempts+=("sudo -n systemctl restart ${service_name} (exit ${exit_code})")
    fi
  fi

  if (( ! succeeded )); then
    if systemctl --user restart "$service_name" >/dev/null 2>&1; then
      echo "Restarted ${service_name} via systemctl --user." >&2
      succeeded=1
    else
      local exit_code=$?
      attempts+=("systemctl --user restart ${service_name} (exit ${exit_code})")
    fi
  fi

  if (( succeeded )); then
    return 0
  fi

  echo "Warning: Unable to restart ${service_name} automatically." >&2
  if ((${#attempts[@]} > 0)); then
    printf '  Tried: %s\n' "${attempts[@]}" >&2
  fi
  echo "Run 'sudo systemctl restart ${service_name}' or review service permissions and logs." >&2
  return 1
}

restart_service
