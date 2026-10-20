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
cp uninstall.sh "$PAYLOAD_DIR$INSTALL_LOCATION/"

# Menu-bar UI as an LSUIElement app bundle. macOS treats a bare executable as
# a background-only process and does not reliably show its menu-bar icon.
UI_APP="$PAYLOAD_DIR$INSTALL_LOCATION/MyPortal Tray.app"
mkdir -p "$UI_APP/Contents/MacOS" "$UI_APP/Contents/Resources"
cp ../../dist/darwin/myportal-tray-ui "$UI_APP/Contents/MacOS/"
chmod +x "$UI_APP/Contents/MacOS/myportal-tray-ui"
# Bundle versions must be numeric: v0.9.7-3-gabc becomes 0.9.7.
SHORT_VERSION="$(printf '%s' "$VERSION" | sed -E 's/^v//; s/[^0-9.].*//; s/\.$//')"
[ -n "$SHORT_VERSION" ] || SHORT_VERSION="0.0.0"
sed "s/__SHORT_VERSION__/$SHORT_VERSION/g" io.myportal.tray.ui.Info.plist > "$UI_APP/Contents/Info.plist"
# The chat shell build generates the shared app icon.
if [ -f ../../chat-shell/build/icon.icns ]; then
    cp ../../chat-shell/build/icon.icns "$UI_APP/Contents/Resources/AppIcon.icns"
    /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string AppIcon" "$UI_APP/Contents/Info.plist"
fi
plutil -lint "$UI_APP/Contents/Info.plist"
# Seal the bundle so macOS ties the executable to its Info.plist. Ad-hoc
# unless a Developer ID Application identity is provided.
if [[ -n "${DEVELOPER_ID_APPLICATION:-}" ]]; then
    codesign --force --options runtime --timestamp --sign "$DEVELOPER_ID_APPLICATION" "$UI_APP"
else
    codesign --force --sign - "$UI_APP"
fi
codesign --verify --strict "$UI_APP"

# Dedicated chat shell: an Electron .app bundle pre-built on a macOS runner.
# The tray UI launches the binary inside this bundle directly for an isolated
# chat window that is fully separate from the user's browser sessions.
if [ -d "../../dist/darwin/myportal-tray-chat.app" ]; then
    cp -R "../../dist/darwin/myportal-tray-chat.app" "$PAYLOAD_DIR$INSTALL_LOCATION/"
    # Ensure the main executable is executable after copy.
    chmod +x "$PAYLOAD_DIR$INSTALL_LOCATION/myportal-tray-chat.app/Contents/MacOS/myportal-tray-chat" 2>/dev/null || true
else
    echo "Warning: dist/darwin/myportal-tray-chat.app not found; chat shell will not be included in this package." >&2
    echo "  Run 'make build-chat-shell-mac' on a macOS host first, or download the chat-shell-darwin CI artifact." >&2
fi

chmod +x "$PAYLOAD_DIR$INSTALL_LOCATION/uninstall.sh"

# Plists.
cp io.myportal.tray.service.plist "$PAYLOAD_DIR/Library/LaunchDaemons/"
cp io.myportal.tray.agent.plist   "$PAYLOAD_DIR/Library/LaunchAgents/"

UNSIGNED_PKG="myportal-tray-${VERSION}-unsigned.pkg"

# pkgbuild marks every .app bundle in the payload relocatable by default, so
# Installer would put the chat shell wherever another copy with the same
# bundle ID already lives instead of in $INSTALL_LOCATION. Pin every bundle.
COMPONENT_PLIST="$(dirname "$PAYLOAD_DIR")/components.plist"
pkgbuild --analyze --root "$PAYLOAD_DIR" "$COMPONENT_PLIST"
i=0
while /usr/libexec/PlistBuddy -c "Print :$i" "$COMPONENT_PLIST" >/dev/null 2>&1; do
    /usr/libexec/PlistBuddy -c "Set :$i:BundleIsRelocatable false" "$COMPONENT_PLIST"
    i=$((i + 1))
done

pkgbuild \
    --root "$PAYLOAD_DIR" \
    --component-plist "$COMPONENT_PLIST" \
    --ownership recommended \
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
