#!/usr/bin/env bash
# Idempotent Redis setup for Debian/Ubuntu install and upgrade paths.
set -euo pipefail

ENV_FILE="${1:?Usage: provision_redis.sh ENV_FILE}"

# Parse configuration as data: env files can contain shell metacharacters.
# Preserve configured URLs, including authentication and remote endpoints.
local_redis=$(python3 - "$ENV_FILE" <<'PY'
import sys
from pathlib import Path
from urllib.parse import urlsplit

path = Path(sys.argv[1])
content = path.read_text(encoding="utf-8")
lines = content.splitlines()
indices = []
url = ""
for index, raw in enumerate(lines):
    line = raw.strip().removeprefix("export ")
    if line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key.strip() == "REDIS_URL":
        indices.append(index)
        value = value.strip()
        if value.startswith(("'", '"')):
            value = value[1:].split(value[0], 1)[0]
        else:
            value = value.split(" #", 1)[0].strip()
        url = value
if not url:
    url = "redis://127.0.0.1:6379/0"
    for index in indices:
        lines[index] = f"REDIS_URL={url}"
    if not indices:
        lines.append(f"REDIS_URL={url}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
endpoint = urlsplit(url)
print("yes" if endpoint.hostname in {"localhost", "127.0.0.1", "::1"} else "no")
PY
)

# A configured remote Redis already supplies the deployment's Redis service.
[[ "$local_redis" == yes ]] || exit 0

if command -v redis-server >/dev/null 2>&1 \
    || [[ "$(dpkg-query -W -f='${Status}' redis-server 2>/dev/null || true)" == "install ok installed" ]]; then
  echo "Redis is already installed; reusing it." >&2
else
  echo "Installing local Redis…" >&2
  apt-get update -qq
  env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq redis-server
fi

# Keep existing Redis configuration intact and start stopped installations.
if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
  systemctl enable --now redis-server.service
elif command -v service >/dev/null 2>&1; then
  service redis-server start
else
  echo "Error: cannot start Redis; no service manager is available." >&2
  exit 1
fi
