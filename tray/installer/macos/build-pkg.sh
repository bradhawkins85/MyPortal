#!/bin/bash
# macOS package build script.
# Requires: Apple Developer ID certificate in keychain, Xcode command-line tools.
#
# Usage: ./build-pkg.sh <version>
# Output: myportal-tray-<version>.pkg (unsigned) or myportal-tray-<version>-signed.pkg
set -euo pipefail

VERSION="${1:?Usage: $0 <version>}"
IDENTIFIER="io.myportal.tray"
INSTALL_LOCATION="/Library/MyPortal/Tray"
PAYLOAD_DIR="$(mktemp -d)/payload"
SCRIPTS_DIR="$(dirname "$0")"

mkdir -p "$PAYLOAD_DIR$INSTALL_LOCATION"
mkdir -p "$PAYLOAD_DIR/Library/LaunchDaemons"
mkdir -p "$PAYLOAD_DIR/Library/LaunchAgents"

# Binaries (cross-compiled by Makefile).
cp ../../dist/darwin/myportal-tray-service "$PAYLOAD_DIR$INSTALL_LOCATION/"
cp ../../dist/darwin/myportal-tray-ui     "$PAYLOAD_DIR$INSTALL_LOCATION/"

# Plists.
cp io.myportal.tray.service.plist "$PAYLOAD_DIR/Library/LaunchDaemons/"
cp io.myportal.tray.agent.plist   "$PAYLOAD_DIR/Library/LaunchAgents/"

UNSIGNED_PKG="myportal-tray-${VERSION}-unsigned.pkg"

pkgbuild \
    --root "$PAYLOAD_DIR" \
    --scripts "$SCRIPTS_DIR" \
    --identifier "$IDENTIFIER" \
    --version "$VERSION" \
    --install-location / \
    "$UNSIGNED_PKG"

FINAL_PKG="myportal-tray-${VERSION}.pkg"

productbuild \
    --package "$UNSIGNED_PKG" \
    "$FINAL_PKG"

echo "Built: $FINAL_PKG"

# Sign + notarize when DEVELOPER_ID_INSTALLER is set.
if [[ -n "${DEVELOPER_ID_INSTALLER:-}" ]]; then
    SIGNED_PKG="myportal-tray-${VERSION}-signed.pkg"
    productsign --sign "$DEVELOPER_ID_INSTALLER" "$FINAL_PKG" "$SIGNED_PKG"
    echo "Signed: $SIGNED_PKG"

    if [[ -n "${APPLE_ID:-}" && -n "${APPLE_TEAM_ID:-}" ]]; then
        xcrun notarytool submit "$SIGNED_PKG" \
            --apple-id "$APPLE_ID" \
            --team-id "$APPLE_TEAM_ID" \
            --password "${APPLE_APP_PASSWORD:?}" \
            --wait
        xcrun stapler staple "$SIGNED_PKG"
        echo "Notarized: $SIGNED_PKG"
    fi
fi
