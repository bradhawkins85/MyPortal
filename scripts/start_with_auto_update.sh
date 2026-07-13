#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
UPDATE_HELPER="${SCRIPT_DIR}/upgrade.sh"

if ! cd "$PROJECT_ROOT"; then
  echo "[uvicorn-wrapper] Failed to change directory to ${PROJECT_ROOT}." >&2
  exit 1
fi
DEFAULT_ATTEMPTS=2
DEFAULT_DELAY=5

detect_bool() {
  local value="$1"
  case "${value,,}" in
    1|true|yes|on) return 0 ;;
    0|false|no|off|"") return 1 ;;
    *) return 1 ;;
  esac
}


normalize_log_level() {
  local value="${1:-}"
  value="${value%%#*}"
  # Trim leading/trailing whitespace.
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  if [[ -z "$value" ]]; then
    return 1
  fi
  case "${value,,}" in
    debug|info|warning|error|critical|trace)
      printf '%s' "${value,,}"
      return 0
      ;;
    warn)
      printf 'warning'
      return 0
      ;;
    fatal)
      printf 'critical'
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

has_uvicorn_log_level_arg() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      --log-level|--log-level=*) return 0 ;;
    esac
  done
  return 1
}

build_uvicorn_command() {
  UVICORN_COMMAND=("$@")
  local resolved_log_level=""
  if resolved_log_level=$(normalize_log_level "${UVICORN_LOG_LEVEL:-}"); then
    :
  elif resolved_log_level=$(normalize_log_level "${LOG_LEVEL:-}"); then
    :
  fi

  if [[ -n "$resolved_log_level" ]] && ! has_uvicorn_log_level_arg "$@"; then
    UVICORN_COMMAND+=(--log-level "$resolved_log_level")
  fi
}

numeric_or_default() {
  local input="$1"
  local fallback="$2"
  if [[ -z "$input" ]]; then
    printf '%s' "$fallback"
    return
  fi
  if [[ "$input" =~ ^[0-9]+$ ]]; then
    printf '%s' "$input"
    return
  fi
  printf '%s' "$fallback"
}

MAX_ATTEMPTS=$(numeric_or_default "${UVICORN_AUTO_UPDATE_ATTEMPTS:-}" "$DEFAULT_ATTEMPTS")
if [[ "$MAX_ATTEMPTS" -lt 1 ]]; then
  MAX_ATTEMPTS=1
fi

RETRY_DELAY=$(numeric_or_default "${UVICORN_AUTO_UPDATE_RETRY_DELAY:-}" "$DEFAULT_DELAY")
ENABLE_UPDATE=0
if detect_bool "${UVICORN_AUTO_UPDATE_ENABLED:-1}"; then
  ENABLE_UPDATE=1
fi

uvicorn_pid=0
forward_signal() {
  local signal="$1"
  if [[ "$uvicorn_pid" -gt 0 ]]; then
    kill -s "$signal" "$uvicorn_pid" 2>/dev/null || true
  fi
}

trap 'forward_signal TERM' TERM
trap 'forward_signal INT' INT
trap 'forward_signal QUIT' QUIT

run_uvicorn() {
  build_uvicorn_command "$@"
  "${UVICORN_COMMAND[@]}" &
  uvicorn_pid=$!
  wait "$uvicorn_pid"
  local status=$?
  uvicorn_pid=0
  return "$status"
}

attempt=1
while (( attempt <= MAX_ATTEMPTS )); do
  echo "[uvicorn-wrapper] Launching application (attempt ${attempt}/${MAX_ATTEMPTS})." >&2
  if run_uvicorn "$@"; then
    exit 0
  fi
  status=$?
  echo "[uvicorn-wrapper] Uvicorn exited with status ${status}." >&2

  if (( attempt >= MAX_ATTEMPTS )) || (( ENABLE_UPDATE == 0 )); then
    echo "[uvicorn-wrapper] Giving up after ${attempt} attempt(s)." >&2
    exit "$status"
  fi

  if [[ ! -x "$UPDATE_HELPER" ]]; then
    echo "[uvicorn-wrapper] Update helper ${UPDATE_HELPER} not found or not executable; aborting auto-update." >&2
    exit "$status"
  fi

  echo "[uvicorn-wrapper] Running upgrade helper before retry." >&2
  if ! "$UPDATE_HELPER" --auto-fallback; then
    upgrade_status=$?
    echo "[uvicorn-wrapper] Upgrade helper failed with status ${upgrade_status}; aborting." >&2
    exit "$status"
  fi

  if (( RETRY_DELAY > 0 )); then
    sleep "$RETRY_DELAY"
  fi

  ((attempt++))
done

exit 1
