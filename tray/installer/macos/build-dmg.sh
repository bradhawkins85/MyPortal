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
cat > "$STAGING_DIR/README.txt" <<README
MyPortal Tray Installer

Open myportal-tray.pkg to install the MyPortal tray service and user agent.

The package installs an uninstaller at:
  /Library/MyPortal/Tray/uninstall.sh
Run it with sudo, optionally adding --purge to remove configuration, state, and logs.

For RMM deployment, use installer/macos/install.sh from the source repository
or download the package directly from your MyPortal server:
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
