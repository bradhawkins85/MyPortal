#!/bin/bash
# macOS DMG build script for the MyPortal Tray installer.
# Requires: hdiutil (macOS only) and a pre-built myportal-tray.pkg.
#
# Usage: ./build-dmg.sh <version> [pkg-path]
# Output: myportal-tray-<version>.dmg
set -euo pipefail

VERSION="${1:?Usage: $0 <version> [pkg-path]}"
PKG_PATH="${2:-myportal-tray-${VERSION}.pkg}"
VOLUME_NAME="MyPortal Tray ${VERSION}"
DMG_NAME="myportal-tray-${VERSION}.dmg"
STAGING_DIR="$(mktemp -d)"

cleanup() {
    rm -rf "$STAGING_DIR"
}
trap cleanup EXIT

if ! command -v hdiutil >/dev/null 2>&1; then
    echo "hdiutil is required to build a macOS DMG. Run this script on macOS." >&2
    exit 1
fi

if [ ! -f "$PKG_PATH" ]; then
    echo "Package not found: $PKG_PATH" >&2
    echo "Run 'make build-pkg' first, or pass the package path as the second argument." >&2
    exit 1
fi

cp "$PKG_PATH" "$STAGING_DIR/myportal-tray.pkg"
cat > "$STAGING_DIR/silent-install.sh" <<'SILENT'
#!/bin/bash
# MyPortal Tray silent installer for the package included in this DMG.
#
# Usage (as root, from the mounted DMG):
#   MYPORTAL_URL='https://portal.example.com' ENROL_TOKEN='TOKEN' ./silent-install.sh
#
# Or edit the values below before running the script:
MYPORTAL_URL="${MYPORTAL_URL:-}"
ENROL_TOKEN="${ENROL_TOKEN:-}"
AUTO_UPDATE="${AUTO_UPDATE:-true}"

set -euo pipefail

: "${MYPORTAL_URL:?Set MYPORTAL_URL to your portal URL, for example https://portal.example.com}"
: "${ENROL_TOKEN:?Set ENROL_TOKEN to the enrolment token from MyPortal}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_PATH="$SCRIPT_DIR/myportal-tray.pkg"
ENV_FILE="/Library/Preferences/io.myportal.tray.env"

if [ "$(id -u)" -ne 0 ]; then
    echo "This installer must be run as root. Re-run with sudo." >&2
    exit 1
fi

if [ ! -f "$PKG_PATH" ]; then
    echo "Package not found next to this script: $PKG_PATH" >&2
    exit 1
fi

echo "Writing MyPortal Tray configuration to $ENV_FILE ..."
umask 077
cat > "$ENV_FILE" <<EOF
MYPORTAL_URL=${MYPORTAL_URL}
ENROL_TOKEN=${ENROL_TOKEN}
AUTO_UPDATE=${AUTO_UPDATE}
EOF
chmod 600 "$ENV_FILE"

echo "Installing $PKG_PATH ..."
installer -pkg "$PKG_PATH" -target /

echo "MyPortal Tray installed successfully."
SILENT
chmod +x "$STAGING_DIR/silent-install.sh"

cat > "$STAGING_DIR/README.txt" <<README
MyPortal Tray Installer

Open myportal-tray.pkg to install the MyPortal tray service and user agent.
The installer prompts for the portal URL and enrolment token when configuration
has not already been written to /Library/Preferences/io.myportal.tray.env.

The package installs an uninstaller at:
  /Library/MyPortal/Tray/uninstall.sh
Run it with sudo, optionally adding --purge to remove configuration, state, and logs.

For silent local deployment from this DMG, run the included script as root:
  sudo MYPORTAL_URL="https://portal.example.com" ENROL_TOKEN="TOKEN" ./silent-install.sh

For RMM deployment that downloads the latest package, use installer/macos/install.sh
from the source repository or download the package directly from your MyPortal server:
  /static/tray/myportal-tray.pkg
README

rm -f "$DMG_NAME"
hdiutil create \
    -volname "$VOLUME_NAME" \
    -srcfolder "$STAGING_DIR" \
    -ov \
    -format UDZO \
    "$DMG_NAME"

echo "Built: $DMG_NAME"
