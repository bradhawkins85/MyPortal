#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
FLAG_DIR="${PROJECT_ROOT}/var/state"
RESTART_FLAG_FILE="${FLAG_DIR}/restart_required.flag"
LOCK_FILE="${FLAG_DIR}/restart.lock"

mkdir -p "$FLAG_DIR"
chmod 750 "$FLAG_DIR" >/dev/null 2>&1 || true

if [[ ! -f "$RESTART_FLAG_FILE" ]]; then
  exit 0
fi

(
  flock -n 200 || exit 0

  if [[ ! -f "$RESTART_FLAG_FILE" ]]; then
    exit 0
  fi

  echo "Update flag found at $RESTART_FLAG_FILE. Running restart helper." >&2
  if "${SCRIPT_DIR}/restart.sh"; then
    rm -f "$RESTART_FLAG_FILE"
    echo "Restart helper completed successfully; cleared $RESTART_FLAG_FILE" >&2
  else
    status=$?
    echo "Error: restart helper exited with status ${status}. Leaving $RESTART_FLAG_FILE in place." >&2
    exit "$status"
  fi
) 200>"$LOCK_FILE"
