#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
VENV_DIR="${PROJECT_ROOT}/.venv"

prepare_git_environment() {
  local current_home="${HOME:-}"
  if [[ -n "$current_home" ]]; then
    if mkdir -p "$current_home/.config/git" >/dev/null 2>&1; then
      return
    fi
  fi

  local fallback_home="${PROJECT_ROOT}/.cache/git-home"
  mkdir -p "$fallback_home/.config/git"
  export HOME="$fallback_home"
  export XDG_CONFIG_HOME="$fallback_home/.config"
  export GIT_CONFIG_GLOBAL="$fallback_home/.gitconfig"
  echo "Redirected Git configuration to ${fallback_home} because the default HOME directory is not writable." >&2
}

prepare_git_environment

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

read_env_var() {
  local key="$1"
  local default_value="${2:-}"

  if [[ -n "${!key:-}" ]]; then
    printf '%s' "${!key}"
    return
  fi

  if [[ -z "$PYTHON_INTERPRETER" || ! -f "${PROJECT_ROOT}/.env" ]]; then
    printf '%s' "$default_value"
    return
  fi

  local value
  value=$(ENV_LOOKUP_KEY="$key" ENV_LOOKUP_DEFAULT="$default_value" PROJECT_ROOT="$PROJECT_ROOT" "$PYTHON_INTERPRETER" - <<'PY'
from __future__ import annotations

import os
from pathlib import Path

key = os.environ["ENV_LOOKUP_KEY"]
default = os.environ.get("ENV_LOOKUP_DEFAULT", "")
env_path = Path(Path(os.environ["PROJECT_ROOT"]) / ".env")

if not env_path.exists():
    print(default)
    raise SystemExit

for raw_line in env_path.read_text(encoding="utf-8").splitlines():
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

ensure_env_default() {
  local interpreter="$1"
  local key="$2"
  local default_value="$3"
  local env_file="${PROJECT_ROOT}/.env"

  if [[ -z "$interpreter" || ! -f "$env_file" ]]; then
    return
  fi

  ENV_DEFAULT_KEY="$key" \
    ENV_DEFAULT_VALUE="$default_value" \
    ENV_DEFAULT_FILE="$env_file" \
    "$interpreter" - <<'PY'
from __future__ import annotations

import os
from pathlib import Path

env_path = Path(os.environ["ENV_DEFAULT_FILE"])
key = os.environ["ENV_DEFAULT_KEY"]
default = os.environ["ENV_DEFAULT_VALUE"]

existing = env_path.read_text(encoding="utf-8")
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

PYTHON_INTERPRETER=$(detect_python_interpreter)

cd "$PROJECT_ROOT"

ensure_env_default "$PYTHON_INTERPRETER" "ENABLE_AUTO_REFRESH" "false"

detect_service_name() {
  local explicit
  explicit=$(read_env_var "SYSTEMD_SERVICE_NAME" "")
  if [[ -n "$explicit" ]]; then
    printf '%s' "$explicit"
    return
  fi
  printf '%s' "myportal"
}

resolve_service_user() {
  local service_name="$1"

  if [[ -n "${MYPORTAL_SERVICE_USER:-}" ]]; then
    printf '%s' "$MYPORTAL_SERVICE_USER"
    return
  fi

  if [[ -n "${SERVICE_USER:-}" ]]; then
    printf '%s' "$SERVICE_USER"
    return
  fi

  local env_override
  env_override=$(read_env_var "SERVICE_USER" "")
  if [[ -n "$env_override" ]]; then
    printf '%s' "$env_override"
    return
  fi

  local systemctl_bin
  if command -v systemctl >/dev/null 2>&1; then
    systemctl_bin=$(command -v systemctl)
  else
    systemctl_bin=""
  fi

  if [[ -n "$systemctl_bin" ]]; then
    local reported_user
    reported_user=$($systemctl_bin show "$service_name" --property=User --value 2>/dev/null | tr -d '\r') || reported_user=""
    if [[ -n "$reported_user" ]]; then
      printf '%s' "$reported_user"
      return
    fi

    local fragment_path
    fragment_path=$($systemctl_bin show "$service_name" --property=FragmentPath --value 2>/dev/null | tr -d '\r') || fragment_path=""
    if [[ -n "$fragment_path" && -f "$fragment_path" ]]; then
      local parsed_user
      parsed_user=$(awk -F'=' '/^User=/{print $2; exit}' "$fragment_path")
      if [[ -n "$parsed_user" ]]; then
        printf '%s' "$parsed_user"
        return
      fi
    fi
  fi

  printf '%s' "$service_name"
}

reset_project_permissions() {
  local service_user="$1"
  if [[ -z "$service_user" ]]; then
    echo "Warning: Unable to determine service user; skipping ownership reset." >&2
    return
  fi

  if ! id "$service_user" >/dev/null 2>&1; then
    echo "Warning: Service user '$service_user' was not found on this system; skipping ownership reset." >&2
    return
  fi

  local service_group
  service_group=$(id -gn "$service_user" 2>/dev/null || true)
  if [[ -z "$service_group" ]]; then
    service_group="$service_user"
  fi

  if chown -R "$service_user:$service_group" "$PROJECT_ROOT"; then
    echo "Reset ownership of ${PROJECT_ROOT} to ${service_user}:${service_group}."
  else
    echo "Warning: Failed to reset ownership of ${PROJECT_ROOT}; please update permissions manually if required." >&2
  fi
}

SERVICE_NAME=$(detect_service_name)
SERVICE_USER=$(resolve_service_user "$SERVICE_NAME")

# Load GitHub credentials from .env in a safe manner
if [[ -f .env ]]; then
  if [[ -z "$PYTHON_INTERPRETER" ]]; then
    echo "Warning: Unable to locate a python interpreter to parse .env credentials. Skipping GitHub authentication." >&2
  else
    while IFS=':' read -r key encoded || [[ -n "${key:-}" ]]; do
      if [[ -z "${key:-}" ]]; then
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
FORCE_RESTART="${FORCE_RESTART:-0}"

reset_project_permissions "$SERVICE_USER"

if [[ "$PRE_PULL_HEAD" != "$POST_PULL_HEAD" ]]; then
  echo "Repository updated to $POST_PULL_HEAD. Run scripts/restart.sh to reinstall dependencies and restart the service."
  "${SCRIPT_DIR}/restart.sh"
elif [[ "$FORCE_RESTART" == "1" ]]; then
  echo "No repository changes detected but FORCE_RESTART=1; running restart helper."
  "${SCRIPT_DIR}/restart.sh"
else
  echo "No changes detected from remote."
fi
