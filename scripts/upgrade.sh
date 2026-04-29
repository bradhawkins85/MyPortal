#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
VENV_DIR="${PROJECT_ROOT}/.venv"

purge_spurious_dist_info() {
  if [[ ! -d "$VENV_DIR" ]]; then
    return
  fi

  local pattern="~?portal-*.dist-info"

  while IFS= read -r -d '' site_packages; do
    while IFS= read -r -d '' artifact; do
      echo "Removing unexpected dist-info artifact: $artifact"
      rm -rf "$artifact"
    done < <(find "$site_packages" -maxdepth 1 -mindepth 1 -name "$pattern" -print0 2>/dev/null)
  done < <(find "$VENV_DIR" -type d -name "site-packages" -print0 2>/dev/null)
}

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

# Clean up local modifications to __pycache__ files before pulling from remote.
# These files are incorrectly tracked in the repository and can cause merge conflicts
# when they are modified locally during normal Python execution.
# This function resets all tracked __pycache__ files to their HEAD version.
clean_pycache_files() {
  git restore .
  echo "Cleaning __pycache__ files to prevent merge conflicts..."
  
  # Reset any local changes to __pycache__ files to prevent merge conflicts
  # git checkout works for both existing and deleted files
  git ls-files '*__pycache__*' 2>/dev/null | while IFS= read -r file; do
    git checkout HEAD -- "$file" 2>/dev/null || true
  done
  
  echo "__pycache__ cleanup complete."
}

AUTO_FALLBACK=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --auto-fallback)
      AUTO_FALLBACK=1
      shift
      ;;
    --help|-h)
      cat <<'USAGE'
Usage: upgrade.sh [--auto-fallback]

Fetch and apply the latest application code from the configured Git remote.

Options:
  --auto-fallback  Indicates the script is running as part of an automated
                   recovery path. The script will avoid signalling the
                   external restart helpers so the caller can manage restarts.
USAGE
      exit 0
      ;;
    *)
      echo "Error: Unknown option '$1'" >&2
      exit 1
      ;;
  esac
done

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

purge_spurious_dist_info
trap purge_spurious_dist_info EXIT

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

# Clean up __pycache__ files before pulling to prevent merge conflicts
clean_pycache_files

perform_git_update() {
  local remote_ref="$1"
  local branch="$2"

  if git pull --ff-only "$remote_ref" "$branch"; then
    return 0
  fi

  echo "Fast-forward pull failed; attempting rebase to integrate remote changes..." >&2
  if git pull --rebase "$remote_ref" "$branch"; then
    return 0
  fi

  echo "Error: Unable to update repository automatically from ${remote_ref} ${branch}." >&2
  echo "Please resolve the divergence manually and re-run the upgrade." >&2
  exit 1
}

if [[ -n "${GITHUB_USERNAME:-}" && -n "${GITHUB_PASSWORD:-}" && "$REMOTE_URL" == https://* ]]; then
  AUTH_REMOTE_URL="https://${GITHUB_USERNAME}:${GITHUB_PASSWORD}@${REMOTE_URL#https://}"
  perform_git_update "$AUTH_REMOTE_URL" main
else
  perform_git_update origin main
fi

POST_PULL_HEAD=$(git rev-parse HEAD)
FORCE_RESTART="$(read_env_var "FORCE_RESTART" "0")"

reset_project_permissions "$SERVICE_USER"

update_version_file() {
  local version
  version=$(git log -1 --format="%cd" --date=format:"%Y%m%d%H%M%S" HEAD)
  printf '%s\n' "$version" >"${PROJECT_ROOT}/version.txt"
  echo "Updated version.txt to ${version}."
}

install_dependencies() {
  if [[ -z "$PYTHON_INTERPRETER" ]]; then
    echo "Warning: Unable to locate a Python interpreter; skipping dependency installation." >&2
    return 1
  fi

  echo "Installing updated dependencies…"
  if ! "$PYTHON_INTERPRETER" -m pip install --upgrade "$PROJECT_ROOT"; then
    echo "Error: Dependency installation failed." >&2
    return 1
  fi
}

run_restart_helper() {
  local status=0
  "${SCRIPT_DIR}/restart.sh" || status=$?
  if [[ "$status" -eq 0 ]]; then
    echo "Restart helper completed successfully."
  else
    echo "Error: restart helper exited with status ${status}." >&2
    exit "$status"
  fi
}

# ---------------------------------------------------------------------------
# Tray app build helpers
# ---------------------------------------------------------------------------

GO_MIN_MAJOR=1
GO_MIN_MINOR=22
GO_BIN=""

go_satisfies_version() {
  local go_bin="$1"
  local version_output
  version_output=$("$go_bin" version 2>/dev/null) || return 1
  local major minor
  if [[ "$version_output" =~ go([0-9]+)\.([0-9]+) ]]; then
    major="${BASH_REMATCH[1]}"
    minor="${BASH_REMATCH[2]}"
    if [[ "$major" -gt "$GO_MIN_MAJOR" ]] || \
       [[ "$major" -eq "$GO_MIN_MAJOR" && "$minor" -ge "$GO_MIN_MINOR" ]]; then
      return 0
    fi
  fi
  return 1
}

detect_go() {
  local -a candidates=("${GOROOT:-/usr/local/go}/bin/go" "go")
  local candidate resolved
  for candidate in "${candidates[@]}"; do
    if [[ "$candidate" == /* ]]; then
      [[ -x "$candidate" ]] && resolved="$candidate" || continue
    else
      command -v "$candidate" >/dev/null 2>&1 && resolved=$(command -v "$candidate") || continue
    fi
    if go_satisfies_version "$resolved"; then
      printf '%s' "$resolved"
      return 0
    fi
  done
  return 1
}

ensure_go_toolchain() {
  local go_bin
  if go_bin=$(detect_go); then
    echo "Go toolchain found: $("$go_bin" version)."
    GO_BIN="$go_bin"
    return 0
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Warning: apt-get not found; cannot install Go automatically." >&2
    echo "Install Go ${GO_MIN_MAJOR}.${GO_MIN_MINOR}+ manually to enable tray app builds: https://go.dev/doc/install" >&2
    return 1
  fi

  echo "Go ${GO_MIN_MAJOR}.${GO_MIN_MINOR}+ not found; installing via apt-get…"
  if ! apt-get update -qq; then
    echo "Warning: apt-get update failed; skipping Go installation." >&2
    return 1
  fi
  if ! apt-get install -y -qq golang-go; then
    echo "Warning: Failed to install golang-go package." >&2
    return 1
  fi

  if go_bin=$(detect_go); then
    echo "Go toolchain installed: $("$go_bin" version)."
    GO_BIN="$go_bin"
    return 0
  fi

  echo "Warning: golang-go installed but Go ${GO_MIN_MAJOR}.${GO_MIN_MINOR}+ was not detected." >&2
  echo "Install Go ${GO_MIN_MAJOR}.${GO_MIN_MINOR}+ manually: https://go.dev/doc/install" >&2
  return 1
}

ensure_make() {
  if command -v make >/dev/null 2>&1; then
    return 0
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Warning: apt-get not found; cannot install make automatically." >&2
    return 1
  fi

  echo "make not found; installing via apt-get…"
  if ! apt-get install -y -qq make; then
    echo "Warning: Failed to install make." >&2
    return 1
  fi
}

build_tray_app() {
  local tray_dir="${PROJECT_ROOT}/tray"
  local static_tray_dir="${PROJECT_ROOT}/app/static/tray"

  if [[ ! -f "${tray_dir}/Makefile" ]]; then
    echo "Tray app Makefile not found at ${tray_dir}/Makefile; skipping tray build."
    return
  fi

  if ! ensure_go_toolchain; then
    echo "Skipping tray app build: Go ${GO_MIN_MAJOR}.${GO_MIN_MINOR}+ toolchain not available." >&2
    return
  fi

  if ! ensure_make; then
    echo "Skipping tray app build: make not available." >&2
    return
  fi

  if [[ -z "$GO_BIN" ]]; then
    echo "Warning: GO_BIN is unset after toolchain detection; skipping tray app build." >&2
    return
  fi

  echo "Building tray app…"
  local go_dir
  go_dir=$(dirname "$GO_BIN")
  if ! (cd "$tray_dir" && PATH="${go_dir}:${PATH}" make build-all); then
    echo "Warning: Tray app build failed." >&2
    return
  fi
  echo "Tray app build complete. Binaries are in ${tray_dir}/dist/."

  # Build Windows MSI installer if WiX v4 is available.
  # Install WiX via: dotnet tool install --global wix
  if command -v wix >/dev/null 2>&1; then
    echo "WiX found; building Windows MSI installer…"
    if (cd "$tray_dir" && make build-msi); then
      echo "MSI installer built: ${tray_dir}/dist/windows/myportal-tray.msi"
    else
      echo "Warning: MSI build failed." >&2
    fi
  else
    echo "wix tool not found; skipping MSI build. Install with: dotnet tool install --global wix" >&2
  fi

  # Copy any built installers to app/static/tray/ so they are served via HTTP.
  mkdir -p "$static_tray_dir"
  local copied=0
  if [[ -f "${tray_dir}/dist/windows/myportal-tray.msi" ]]; then
    cp "${tray_dir}/dist/windows/myportal-tray.msi" "${static_tray_dir}/myportal-tray.msi"
    echo "Copied myportal-tray.msi → app/static/tray/"
    copied=1
  fi
  if [[ -f "${tray_dir}/dist/darwin/myportal-tray.pkg" ]]; then
    cp "${tray_dir}/dist/darwin/myportal-tray.pkg" "${static_tray_dir}/myportal-tray.pkg"
    echo "Copied myportal-tray.pkg → app/static/tray/"
    copied=1
  fi
  if [[ "$copied" -eq 0 ]]; then
    echo "No installer packages found to copy to app/static/tray/." >&2
  fi
}

if [[ "$PRE_PULL_HEAD" != "$POST_PULL_HEAD" ]]; then
  echo "Repository updated to $POST_PULL_HEAD."
  update_version_file
  install_dependencies
  build_tray_app
  if [[ "$AUTO_FALLBACK" -eq 0 ]]; then
    run_restart_helper
  else
    echo "Auto-fallback mode detected; caller will relaunch the service." >&2
  fi
elif [[ "$FORCE_RESTART" == "1" ]]; then
  echo "No repository changes detected but FORCE_RESTART=1; reinstalling dependencies and restarting service."
  update_version_file
  install_dependencies
  build_tray_app
  if [[ "$AUTO_FALLBACK" -eq 0 ]]; then
    run_restart_helper
  else
    echo "Auto-fallback mode detected; caller responsible for restart handling." >&2
  fi
else
  echo "No changes detected from remote."
fi
