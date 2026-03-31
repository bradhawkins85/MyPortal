#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
FLAG_DIR="${PROJECT_ROOT}/var/state"
UPDATE_FLAG_FILE="${FLAG_DIR}/system_update.flag"
LOCK_FILE="${FLAG_DIR}/system_update.lock"

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

  echo "Update flag found at $UPDATE_FLAG_FILE. Running upgrade helper." >&2
  if "${SCRIPT_DIR}/upgrade.sh"; then
    rm -f "$UPDATE_FLAG_FILE"
    echo "Upgrade helper completed successfully; cleared $UPDATE_FLAG_FILE" >&2
  else
    status=$?
    echo "Error: upgrade helper exited with status ${status}. Leaving $UPDATE_FLAG_FILE in place." >&2
    exit "$status"
  fi
) 200>"$LOCK_FILE"
