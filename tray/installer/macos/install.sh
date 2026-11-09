#!/bin/bash
# MyPortal Tray — macOS one-liner RMM deployment script
# Copy-paste this into your RMM script (SyncroRMM / TacticalRMM) after
# setting the MYPORTAL_URL and ENROL_TOKEN variables.
#
# Usage (as root):
#   MYPORTAL_URL='https://portal.example.com' ENROL_TOKEN='TOKEN' bash install.sh
#
set -euo pipefail

: "${MYPORTAL_URL:?MYPORTAL_URL must be set}"
: "${ENROL_TOKEN:?ENROL_TOKEN must be set}"
: "${AUTO_UPDATE:=true}"

case "$MYPORTAL_URL" in
  https://*) ;;
  *)
    if [ "${MYPORTAL_ALLOW_HTTP:-}" != "1" ]; then
      echo "MYPORTAL_URL must start with https:// (set MYPORTAL_ALLOW_HTTP=1 to override)" >&2
      exit 1
    fi
    ;;
esac

# Files created from here on are readable by root only.
umask 077

PKG_URL="${MYPORTAL_URL}/static/tray/myportal-tray.pkg"
# A fresh, root-only folder: a fixed name in /tmp could be created or swapped
# by another local user between the download and the install.
WORK_DIR="$(mktemp -d /tmp/myportal-tray.XXXXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT
PKG_PATH="$WORK_DIR/myportal-tray.pkg"
ENV_FILE="/Library/Preferences/io.myportal.tray.env"

CURL_PROTO="=https"
[ "${MYPORTAL_ALLOW_HTTP:-}" = "1" ] && CURL_PROTO="=https,http"

echo "Downloading MyPortal Tray from $PKG_URL ..."
curl -fsSL --proto "$CURL_PROTO" --proto-redir "$CURL_PROTO" "$PKG_URL" -o "$PKG_PATH"

echo "Writing configuration to $ENV_FILE ..."
rm -f "$ENV_FILE"
cat > "$ENV_FILE" <<EOF
MYPORTAL_URL=${MYPORTAL_URL}
ENROL_TOKEN=${ENROL_TOKEN}
AUTO_UPDATE=${AUTO_UPDATE}
EOF
chmod 600 "$ENV_FILE"

echo "Installing package ..."
installer -pkg "$PKG_PATH" -target /

echo "MyPortal Tray installed successfully."
