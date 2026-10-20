#!/bin/bash
# MyPortal built-in: switch RustDesk on for a remote session (Linux; macOS uses
# rustdesk_activate_macos.sh).
#
# MyPortal sets these for each run:
#   MYPORTAL_RUSTDESK_PASSWORD  one-time password for this session
#   RUSTDESK_CFG                server config string (RUSTDESK_CFG in MyPortal's .env)
#
# Applies the config, starts the service, sets the password and reports the ID.
set -euo pipefail

if [ -x /Applications/RustDesk.app/Contents/MacOS/RustDesk ]; then
  RUSTDESK=/Applications/RustDesk.app/Contents/MacOS/RustDesk
elif command -v rustdesk >/dev/null 2>&1; then
  RUSTDESK="$(command -v rustdesk)"
else
  echo "RustDesk is not installed on this device." >&2
  exit 1
fi

if [ -n "${RUSTDESK_CFG:-}" ]; then
  echo "Applying the RustDesk server config"
  "$RUSTDESK" --config "$RUSTDESK_CFG" >/dev/null 2>&1 || true
fi

echo "Starting the RustDesk service"
if command -v systemctl >/dev/null 2>&1; then
  systemctl enable --now rustdesk >/dev/null 2>&1 || true
  systemctl restart rustdesk >/dev/null 2>&1 || true
elif command -v launchctl >/dev/null 2>&1; then
  launchctl kickstart -k system/com.carriez.RustDesk_service >/dev/null 2>&1 || true
fi
sleep 3

echo "Setting a new session password"
"$RUSTDESK" --password "$MYPORTAL_RUSTDESK_PASSWORD" >/dev/null 2>&1

ID=""
for _ in $(seq 1 15); do
  ID="$("$RUSTDESK" --get-id 2>/dev/null | tr -d '[:space:]' || true)"
  [ -n "$ID" ] && break
  sleep 3
done
if [ -z "$ID" ]; then
  echo "RustDesk did not report its ID." >&2
  exit 1
fi
echo "##myportal[session.id]=$ID"
