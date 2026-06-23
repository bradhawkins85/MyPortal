# MyPortal Tray App — Go Client

Cross-platform (Windows + macOS) tray application for the MyPortal helpdesk portal. Deployed via RMM; provides a server-driven system-tray menu, environment-variable display, and a push-to-device chat channel.

## Repository layout

```
tray/
├── service/          Privileged background daemon (Windows Service / macOS LaunchDaemon)
├── ui/               Per-session tray UI agent (runs as the logged-in user)
├── internal/
│   ├── api/          HTTP + WebSocket client for the MyPortal server
│   ├── config/       Platform-aware config loader (registry / plist / env)
│   ├── ipc/          Local IPC between service and UI agent
│   ├── logger/       Structured logger with file rotation
│   ├── notify/       Push-notification helpers
│   └── updater/      Auto-update checker (6-hour timer, deferred install)
├── chat-shell/       Minimal Electron chat window (dedicated app for support chat)
│   ├── main.js       Electron main process — session isolation + window management
│   ├── preload.js    Security preload (context isolation)
│   └── package.json  electron-builder config for Windows portable exe + macOS .app
├── scripts/          Text-only build helpers, including macOS icon generation
├── installer/
│   ├── windows/      WiX v7 MSI project + PowerShell RMM deployment script
│   └── macos/        pkgbuild scripts + LaunchDaemon/LaunchAgent plists + bash RMM script
├── Makefile          Cross-compile Windows + macOS from Linux CI (CGO=0 + nowebview tag)
└── .github/workflows/tray-build.yml   Build, test, and release workflow
```

## Build

### Prerequisites

- Go 1.22+
- For native UI builds: CGO toolchain for the target platform
- For MSI packaging: .NET SDK 8+ and WiX v7 (`dotnet tool install --global wix --version "7.*"`).
  WiX v7 requires accepting the FireGiant OSMF EULA — the Makefile passes
  `-acceptEula wix7` per https://docs.firegiant.com/wix/osmf/.
- For macOS chat-shell icon and installer packaging: Xcode command-line tools with `iconutil`, `pkgbuild`, `productbuild`, and `hdiutil` (macOS only)
- For chat shell: **Node.js 20+** and npm (required only on the native host runner for each platform)

### Cross-compile (CGO=0, no webview — for RMM deployment)

```sh
cd tray
make build-all         # Windows amd64 + macOS amd64 + macOS arm64
make build-windows
make build-darwin-amd64
make build-darwin-arm64
```

Binaries land in `dist/<platform>/`.

### Build the macOS app icon

The macOS chat-shell app icon is generated during the build from a text-only
recipe so binary icon assets are not committed to the repository. Run this on
macOS with Xcode Command Line Tools installed:

```sh
cd tray
make build-macos-icons
# or, from tray/chat-shell:
npm run build:icons:mac
```

The target creates `tray/chat-shell/build/icon.icns`, which electron-builder
uses for the packaged `myportal-tray-chat.app`. The generated `.icns` and
`.iconset` intermediates are build outputs and should not be committed.

### Build the dedicated chat shell

The chat shell is a minimal Electron app that opens the MyPortal support chat
in an isolated window completely separate from the user's browser sessions.
It **must be built on the target platform** (electron-builder constraint). The macOS build runs the icon generation script before invoking electron-builder.

```sh
# On a Windows host — produces dist/windows/chat-shell/ with the
# myportal-tray-chat.exe executable and persistent Electron runtime files
make build-chat-shell-win

# On a macOS host — produces dist/darwin/myportal-tray-chat.app
make build-chat-shell-mac
```

In CI these targets run as separate jobs on `windows-latest` and `macos-latest`
runners respectively. The resulting artifacts are downloaded into `dist/` before
the MSI / .pkg installer jobs run.

### Build installer packages

```sh
# Windows MSI (requires WiX v7 on a Windows host — WiX does not support
# building on Linux/macOS, see wixtoolset/issues#7154). The Makefile
# automatically passes `-acceptEula wix7` to satisfy the FireGiant OSMF
# EULA (https://docs.firegiant.com/wix/osmf/).
# Requires dist/windows/chat-shell/ from build-chat-shell-win.
make build-msi          # produces dist/windows/myportal-tray.msi

# macOS .pkg (requires pkgbuild/productbuild — macOS only)
# Requires dist/darwin/myportal-tray-chat.app from build-chat-shell-mac.
make build-pkg          # produces dist/darwin/myportal-tray.pkg

# macOS .dmg wrapping the .pkg (requires hdiutil — macOS only)
make build-dmg          # produces dist/darwin/myportal-tray.dmg

# Build all binaries + MSI (+ .pkg and .dmg on macOS)
make package-all
```

The built installers must be copied to `app/static/tray/` on the MyPortal server so
they are served at `/static/tray/myportal-tray.msi`, `/static/tray/myportal-tray.pkg`, and `/static/tray/myportal-tray.dmg`.
The `scripts/upgrade.sh` handles this automatically: Windows hosts can build the MSI when WiX is installed, and macOS hosts can build the PKG/DMG when Xcode command-line tools are installed. On Linux hosts, build the installers separately on their native platforms and copy them into `app/static/tray/`.

### With native webview (requires CGO)

Remove the `nowebview` build tag and provide the appropriate CGO cross-compiler.  
See `Makefile` comments for details.

## Tests

```sh
cd tray
go test -tags nowebview -count=1 ./...
```

## Chat client architecture

When a user clicks **Support Chat** in the tray menu, `openChatWindow` uses a
three-tier launch strategy:

| Tier | Method | Isolation |
|------|--------|-----------|
| 1 | **Dedicated chat shell** (`myportal-tray-chat`) | Full — Electron `persist:myportal-tray-chat` session partition, completely separate from all browsers; Windows installs use an unpacked Electron runtime so DLL/EXE payloads persist under `%ProgramFiles%\MyPortalTray\chat-shell` instead of extracting to `%TEMP%` on each launch |
| 2 | Chromium-based browser in `--app=` mode with `--user-data-dir` isolation | Partial — separate profile but same browser binary |
| 3 | Default system browser | None — shares user's browser session |

The server-side `chat_client_mode` config field (set per device in the admin UI)
controls this behaviour:

| Value | Behaviour |
|-------|-----------|
| `""` / `"app"` (default) | Try tier 1 → 2 → 3 in order |
| `"browser"` | Skip directly to tier 3 (rollout safety valve) |
| `"shell"` | Require tier 1; warn and abort if the shell is absent (no silent fallback) |

The chat shell binary is installed to:
- **Windows**: `%ProgramFiles%\MyPortalTray\chat-shell\myportal-tray-chat.exe` (current unpacked Electron install) or `%ProgramFiles%\MyPortalTray\myportal-tray-chat.exe` (legacy single-file install fallback)
- **macOS**: `/Library/MyPortal/Tray/myportal-tray-chat.app/Contents/MacOS/myportal-tray-chat`

## Configuration

### Windows

Registry key: `HKLM\Software\MyPortal\Tray`

| Value        | Type   | Description                              |
|--------------|--------|------------------------------------------|
| `PortalURL`  | REG_SZ | Full URL of the MyPortal server          |
| `EnrolToken` | REG_SZ | Per-company install token from admin UI  |
| `AutoUpdate` | REG_SZ | `true` / `false` (default `true`)        |

### macOS

File: `/Library/Preferences/io.myportal.tray.env`

```
MYPORTAL_URL=https://portal.example.com
ENROL_TOKEN=<token>
AUTO_UPDATE=true
```

## Architecture

See [docs/tray_app.md](../docs/tray_app.md) for the full architecture document.

## Deployment

### Windows (PowerShell, RMM one-liner)

```powershell
.\installer\windows\install.ps1 -PortalURL 'https://portal.example.com' -EnrolToken 'TOKEN'
```

### macOS (bash, RMM one-liner)

```bash
MYPORTAL_URL='https://portal.example.com' ENROL_TOKEN='TOKEN' bash installer/macos/install.sh
```


### macOS uninstaller

The macOS package bundles an uninstaller at `/Library/MyPortal/Tray/uninstall.sh`:

```bash
sudo /Library/MyPortal/Tray/uninstall.sh
# Remove config, enrolment state, and logs as well:
sudo /Library/MyPortal/Tray/uninstall.sh --purge
```

The default uninstall stops launchd jobs and removes installed binaries/plists while preserving configuration, state, and logs for troubleshooting or reinstall.

## Security model

- The install token is **single-company** and **rotatable** from the admin UI.
- The device exchanges the install token for a **long-lived per-device auth token** stored in the platform credential store (DPAPI / Keychain System). The install token is then discarded.
- Env-var values are read client-side and only reported when the server explicitly lists them in the allowlist. The server never requests them wholesale.
- The WebSocket endpoint authenticates via the per-device bearer token; revoked devices are rejected immediately.
- The chat shell runs in an isolated Electron session partition (`persist:myportal-tray-chat`) and only loads URLs from the configured portal origin; external links are delegated to the system browser.

## Phases

| Phase | Status | Notes |
|-------|--------|-------|
| 1 — Server foundation | ✅ Shipped | Migrations, REST endpoints, admin UI |
| 2 — Realtime / chat-start | ✅ Shipped | WS handler, tech-initiated chat |
| 3 — Windows client MVP | ✅ Shipped | Service + UI agent + WiX MSI + nowebview build |
| 4 — macOS client MVP | ✅ Shipped | LaunchDaemon + LaunchAgent + pkgbuild |
| 5 — Hardening | ✅ Shipped | Auto-update, diagnostics upload, version endpoint |
| 6 — Dedicated chat shell | ✅ Shipped | Electron chat shell; 3-tier launch strategy; `chat_client_mode` server flag |
| 7 — Polish | 🔄 Partial | Notification push, versions admin page; localization deferred |
