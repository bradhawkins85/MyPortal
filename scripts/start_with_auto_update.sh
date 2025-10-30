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
  "$@" &
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
