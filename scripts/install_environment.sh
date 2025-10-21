#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
ENV_FILE="${PROJECT_ROOT}/.env"
ENV_TEMPLATE="${PROJECT_ROOT}/.env.example"
VENV_DIR="${PROJECT_ROOT}/.venv"

usage() {
  cat <<'USAGE'
Usage: install_environment.sh <environment>

Prepare a MyPortal installation for the specified environment. Supported
environments are:
  production   Installs dependencies in a dedicated virtual environment using
               regular (non-editable) mode.
  development  Installs dependencies in editable mode to support local
               iteration alongside production deployments.
USAGE
}

if [[ $# -lt 1 ]]; then
  usage >&2
  exit 1
fi

ENVIRONMENT="$1"
case "$ENVIRONMENT" in
  production|development)
    ;;
  *)
    echo "Error: Unsupported environment '${ENVIRONMENT}'." >&2
    usage >&2
    exit 1
    ;;
esac

select_system_python() {
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

SYSTEM_PYTHON=$(select_system_python)

if [[ -z "$SYSTEM_PYTHON" ]]; then
  echo "Error: Python 3 is required to run the installer." >&2
  exit 1
fi

ensure_env_file() {
  if [[ -f "$ENV_FILE" ]]; then
    return
  fi

  if [[ ! -f "$ENV_TEMPLATE" ]]; then
    echo "Error: ${ENV_TEMPLATE} template not found." >&2
    exit 1
  fi

  cp "$ENV_TEMPLATE" "$ENV_FILE"
  echo "Created ${ENV_FILE} from template." >&2
}

ensure_env_default() {
  local key="$1"
  local default_value="$2"

  if [[ ! -f "$ENV_FILE" ]]; then
    return
  fi

  ENV_DEFAULT_KEY="$key" \
    ENV_DEFAULT_VALUE="$default_value" \
    ENV_DEFAULT_FILE="$ENV_FILE" \
    "$SYSTEM_PYTHON" - <<'PY'
from __future__ import annotations

import os
from pathlib import Path

env_path = Path(os.environ["ENV_DEFAULT_FILE"])
key = os.environ["ENV_DEFAULT_KEY"]
default = os.environ["ENV_DEFAULT_VALUE"]

existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
for raw_line in existing.splitlines():
    stripped = raw_line.strip()
    if not stripped or stripped.startswith("#") or "=" not in raw_line:
        continue
    name, _ = raw_line.split("=", 1)
    if name.strip() == key:
        break
else:
    suffix = "" if not existing or existing.endswith("\n") else "\n"
    env_path.write_text(existing + f"{suffix}{key}={default}\n", encoding="utf-8")
PY
}

ensure_virtualenv() {
  if [[ -d "$VENV_DIR" ]]; then
    return
  fi

  "$SYSTEM_PYTHON" -m venv "$VENV_DIR"
  echo "Created virtual environment at ${VENV_DIR}." >&2
}

venv_python() {
  if [[ -x "${VENV_DIR}/bin/python" ]]; then
    printf '%s' "${VENV_DIR}/bin/python"
    return
  fi
  if [[ -x "${VENV_DIR}/Scripts/python.exe" ]]; then
    printf '%s' "${VENV_DIR}/Scripts/python.exe"
    return
  fi
  printf ''
}

install_dependencies() {
  local python_bin
  python_bin=$(venv_python)

  if [[ -z "$python_bin" ]]; then
    echo "Error: Unable to locate virtualenv python interpreter." >&2
    exit 1
  fi

  "$python_bin" -m pip install --upgrade pip
  if [[ "$ENVIRONMENT" == "development" ]]; then
    "$python_bin" -m pip install --upgrade -e "$PROJECT_ROOT"
  else
    "$python_bin" -m pip install --upgrade "$PROJECT_ROOT"
  fi
}

ensure_env_file
ensure_env_default "ENABLE_AUTO_REFRESH" "false"
ensure_virtualenv
install_dependencies

cat <<MESSAGE
MyPortal ${ENVIRONMENT} environment is ready.
- Environment file: ${ENV_FILE}
- Virtualenv: ${VENV_DIR}

Remember to configure system services (e.g. systemd) and run database migrations
on startup. The application automatically applies migrations during launch.
MESSAGE
