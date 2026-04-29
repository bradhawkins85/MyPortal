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
├── installer/
│   ├── windows/      WiX v4 MSI project + PowerShell RMM deployment script
│   └── macos/        pkgbuild scripts + LaunchDaemon/LaunchAgent plists + bash RMM script
├── Makefile          Cross-compile Windows + macOS from Linux CI (CGO=0 + nowebview tag)
└── .github/workflows/tray-build.yml   Build, test, and release workflow
```

## Build

### Prerequisites

- Go 1.22+
- For native UI builds: CGO toolchain for the target platform

### Cross-compile (CGO=0, no webview — for RMM deployment)

```sh
cd tray
make build-all         # Windows amd64 + macOS amd64 + macOS arm64
make build-windows
make build-darwin-amd64
make build-darwin-arm64
```

Binaries land in `dist/<platform>/`.

### With native webview (requires CGO)

Remove the `nowebview` build tag and provide the appropriate CGO cross-compiler.  
See `Makefile` comments for details.

## Tests

```sh
cd tray
go test -tags nowebview -count=1 ./...
```

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

## Security model

- The install token is **single-company** and **rotatable** from the admin UI.
- The device exchanges the install token for a **long-lived per-device auth token** stored in the platform credential store (DPAPI / Keychain System). The install token is then discarded.
- Env-var values are read client-side and only reported when the server explicitly lists them in the allowlist. The server never requests them wholesale.
- The WebSocket endpoint authenticates via the per-device bearer token; revoked devices are rejected immediately.

## Phases

| Phase | Status | Notes |
|-------|--------|-------|
| 1 — Server foundation | ✅ Shipped | Migrations, REST endpoints, admin UI |
| 2 — Realtime / chat-start | ✅ Shipped | WS handler, tech-initiated chat |
| 3 — Windows client MVP | ✅ Shipped | Service + UI agent + WiX MSI + nowebview build |
| 4 — macOS client MVP | ✅ Shipped | LaunchDaemon + LaunchAgent + pkgbuild |
| 5 — Hardening | ✅ Shipped | Auto-update, diagnostics upload, version endpoint |
| 6 — Polish | 🔄 Partial | Notification push, versions admin page; localization deferred |
