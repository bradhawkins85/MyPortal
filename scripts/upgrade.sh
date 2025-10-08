#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
cd "$PROJECT_ROOT"

# Load GitHub credentials from .env in a safe manner
if [ -f .env ]; then
  while IFS=':' read -r key encoded || [ -n "$key" ]; do
    if [ -z "${key:-}" ]; then
      continue
    fi
    value=$(printf '%s' "$encoded" | base64 --decode)
    case "$key" in
      GITHUB_USERNAME) GITHUB_USERNAME="$value" ;;
      GITHUB_PASSWORD) GITHUB_PASSWORD="$value" ;;
    esac
  done < <(
    python - <<'PY'
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

REMOTE_URL=$(git config --get remote.origin.url || true)

PRE_PULL_HEAD=$(git rev-parse HEAD)

if [[ -n "${GITHUB_USERNAME:-}" && -n "${GITHUB_PASSWORD:-}" && "$REMOTE_URL" == https://* ]]; then
  AUTH_REMOTE_URL="https://${GITHUB_USERNAME}:${GITHUB_PASSWORD}@${REMOTE_URL#https://}"
  git pull "$AUTH_REMOTE_URL" main
else
  git pull origin main
fi

POST_PULL_HEAD=$(git rev-parse HEAD)

if [[ "$PRE_PULL_HEAD" != "$POST_PULL_HEAD" ]]; then
  echo "Repository updated to $POST_PULL_HEAD. Installing dependencies and restarting service."
  pip install -e .
  systemctl restart myportal.service
else
  echo "No changes detected from remote. Skipping dependency installation and service restart."
fi
