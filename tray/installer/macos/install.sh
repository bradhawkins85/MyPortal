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

PKG_URL="${MYPORTAL_URL}/static/tray/myportal-tray.pkg"
PKG_PATH="/tmp/myportal-tray.pkg"
ENV_FILE="/Library/Preferences/io.myportal.tray.env"

echo "Downloading MyPortal Tray from $PKG_URL ..."
curl -fsSL "$PKG_URL" -o "$PKG_PATH"

echo "Writing configuration to $ENV_FILE ..."
cat > "$ENV_FILE" <<EOF
MYPORTAL_URL=${MYPORTAL_URL}
ENROL_TOKEN=${ENROL_TOKEN}
AUTO_UPDATE=${AUTO_UPDATE}
EOF
chmod 600 "$ENV_FILE"

echo "Installing package ..."
installer -pkg "$PKG_PATH" -target /

echo "MyPortal Tray installed successfully."
