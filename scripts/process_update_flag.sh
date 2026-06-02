#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
FLAG_DIR="${PROJECT_ROOT}/var/state"
UPDATE_FLAG_FILE="${FLAG_DIR}/system_update.flag"
LOCK_FILE="${FLAG_DIR}/system_update.lock"

normalise_upgrade_mode() {
  local raw="${1:-}"
  case "${raw,,}" in
    graceful|rolling|restart)
      printf '%s' "${raw,,}"
      ;;
    *)
      printf '%s' "graceful"
      ;;
  esac
}

read_flag_var() {
  local key="$1"
  if [[ ! -f "$UPDATE_FLAG_FILE" ]]; then
    return
  fi
  awk -F'=' -v lookup="$key" '
    $0 !~ /^[[:space:]]*#/ && index($0, "=") > 0 {
      current=$1
      sub(/^[[:space:]]+/, "", current)
      sub(/[[:space:]]+$/, "", current)
      if (current == lookup) {
        value=substr($0, index($0, "=") + 1)
        sub(/^[[:space:]]+/, "", value)
        sub(/[[:space:]]+$/, "", value)
        print value
        exit
      }
    }
  ' "$UPDATE_FLAG_FILE"
}

resolve_upgrade_mode() {
  local requested
  requested=$(read_flag_var "requested_mode")
  if [[ -n "$requested" ]]; then
    normalise_upgrade_mode "$requested"
    return
  fi
  normalise_upgrade_mode "${APP_UPGRADE_MODE:-graceful}"
}

build_upgrade_command() {
  local mode="$1"
  case "$mode" in
    graceful) printf '%s' "\"${SCRIPT_DIR}/upgrade.sh\" --graceful" ;;
    rolling) printf '%s' "\"${SCRIPT_DIR}/upgrade.sh\" --rolling" ;;
    restart) printf '%s' "\"${SCRIPT_DIR}/upgrade.sh\" --restart" ;;
  esac
}

mkdir -p "$FLAG_DIR"
chmod 750 "$FLAG_DIR" >/dev/null 2>&1 || true

if [[ ! -f "$UPDATE_FLAG_FILE" ]]; then
  exit 0
fi

(
  flock -n 200 || exit 0

  if [[ ! -f "$UPDATE_FLAG_FILE" ]]; then
    exit 0
  fi

  upgrade_mode=$(resolve_upgrade_mode)
  echo "Update flag found at $UPDATE_FLAG_FILE. Running upgrade helper in ${upgrade_mode} mode." >&2
  if bash -lc "$(build_upgrade_command "$upgrade_mode")"; then
    rm -f "$UPDATE_FLAG_FILE"
    echo "Upgrade helper completed successfully; cleared $UPDATE_FLAG_FILE" >&2
  else
    status=$?
    echo "Error: upgrade helper exited with status ${status}. Leaving $UPDATE_FLAG_FILE in place." >&2
    exit "$status"
  fi
) 200>"$LOCK_FILE"
