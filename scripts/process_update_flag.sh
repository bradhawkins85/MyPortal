#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
SHARED_STATE_DIR="${MYPORTAL_SHARED_ROOT:-/opt/myportal/shared}/state"
SERVICE_USER="myportal"
# Immutable releases link var/ to the shared tree, so the application writes
# its update request to the shared state directory. Checkouts that predate the
# immutable-release layout keep the flag inside the checkout.
if [[ -d "$SHARED_STATE_DIR" ]]; then
  FLAG_DIR="$SHARED_STATE_DIR"
  LEGACY_FLAG_DIR=false
else
  FLAG_DIR="${PROJECT_ROOT}/var/state"
  LEGACY_FLAG_DIR=true
fi
UPDATE_FLAG_FILE="${FLAG_DIR}/system_update.flag"
LOCK_FILE="${FLAG_DIR}/system_update.lock"
REPORT_HELPER="${SCRIPT_DIR}/system_update_report.py"

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

validate_flag_file() {
  if [[ -L "$UPDATE_FLAG_FILE" ]]; then
    echo "Error: Refusing to process symlinked update flag: $UPDATE_FLAG_FILE" >&2
    exit 1
  fi

  if command -v stat >/dev/null 2>&1; then
    local flag_mode
    local project_mode
    local flag_owner
    local project_owner
    flag_mode=$(stat -c '%a' "$UPDATE_FLAG_FILE" 2>/dev/null || true)
    project_mode=$(stat -c '%a' "$PROJECT_ROOT" 2>/dev/null || true)
    flag_owner=$(stat -c '%u' "$UPDATE_FLAG_FILE" 2>/dev/null || true)
    project_owner=$(stat -c '%u' "$PROJECT_ROOT" 2>/dev/null || true)

    if [[ -n "$flag_mode" ]] && (( (8#$flag_mode & 8#22) != 0 )); then
      echo "Error: Refusing to process insecure update flag permissions ($flag_mode)." >&2
      exit 1
    fi

    if [[ -n "$project_mode" ]] && (( (8#$project_mode & 2) != 0 )); then
      echo "Error: Refusing to process updates from a world-writable project root ($PROJECT_ROOT)." >&2
      exit 1
    fi

    local service_uid
    service_uid=$(id -u "$SERVICE_USER" 2>/dev/null || true)
    if [[ -n "$flag_owner" && -n "$project_owner" && "$flag_owner" != "0" && "$flag_owner" != "$project_owner" && "$flag_owner" != "$service_uid" ]]; then
      echo "Error: Refusing to process update flag owned by unexpected uid $flag_owner." >&2
      exit 1
    fi
  fi
}

if [[ "$LEGACY_FLAG_DIR" == true ]]; then
  mkdir -p "$FLAG_DIR"
  chmod 750 "$FLAG_DIR" >/dev/null 2>&1 || true
fi

if [[ ! -f "$UPDATE_FLAG_FILE" ]]; then
  exit 0
fi

validate_flag_file

(
  flock -n 200 || exit 0

  if [[ ! -f "$UPDATE_FLAG_FILE" ]]; then
    exit 0
  fi

  upgrade_mode=$(resolve_upgrade_mode)
  update_id=$(read_flag_var "update_id")
  output_file=$(mktemp "${FLAG_DIR}/system-update-output.XXXXXX")
  chmod 600 "$output_file"
  trap 'rm -f "$output_file"' EXIT
  progress_pid=""
  if [[ -n "$update_id" ]]; then
    PYTHONPATH="$PROJECT_ROOT" python3 "$REPORT_HELPER" "$update_id" running
    # Publish the output so far while the upgrade runs, so administrators can
    # follow it live from the System updates page.
    (
      # Finish an in-flight report before exiting so it can never land after
      # the final result; "sleep & wait" lets the signal interrupt the pause.
      trap 'exit 0' TERM
      while :; do
        sleep "${SYSTEM_UPDATE_PROGRESS_INTERVAL:-5}" & wait $!
        PYTHONPATH="$PROJECT_ROOT" python3 "$REPORT_HELPER" "$update_id" running --output-file "$output_file" >/dev/null 2>&1 || true
      done
    ) &
    progress_pid=$!
    trap 'kill "$progress_pid" 2>/dev/null || true; rm -f "$output_file"' EXIT
  fi
  stop_progress_reports() {
    if [[ -n "$progress_pid" ]]; then
      kill "$progress_pid" 2>/dev/null || true
      wait "$progress_pid" 2>/dev/null || true
      progress_pid=""
    fi
  }
  echo "Update flag found at $UPDATE_FLAG_FILE. Running upgrade helper in ${upgrade_mode} mode." >&2
  if bash -lc "$(build_upgrade_command "$upgrade_mode")" > >(tee -a "$output_file") 2> >(tee -a "$output_file" >&2); then
    stop_progress_reports
    rm -f "$UPDATE_FLAG_FILE"
    if [[ -n "$update_id" ]]; then
      PYTHONPATH="$PROJECT_ROOT" python3 "$REPORT_HELPER" "$update_id" succeeded --output-file "$output_file"
    fi
    echo "Upgrade helper completed successfully; cleared $UPDATE_FLAG_FILE" >&2
  else
    status=$?
    stop_progress_reports
    # A future schedule may retry, but this request is terminal. Removing the
    # flag prevents an interrupted/failed update from looping forever.
    rm -f "$UPDATE_FLAG_FILE"
    if [[ -n "$update_id" ]]; then
      PYTHONPATH="$PROJECT_ROOT" python3 "$REPORT_HELPER" "$update_id" failed --output-file "$output_file" --error "Upgrade helper exited with status ${status}."
    fi
    echo "Error: upgrade helper exited with status ${status}. Cleared the consumed update flag." >&2
    exit "$status"
  fi
) 200>"$LOCK_FILE"
