# MyPortal Tray App

A cross-platform (Windows + macOS) tray application that gives end-users a
branded helpdesk shortcut, exposes server-driven custom menus, and keeps a
chat channel open with the helpdesk team. The tray app is **deployed
independently** via SyncroRMM / TacticalRMM — it is **not** bundled with
the MyPortal server.

This document covers the architecture, security model, RMM deployment, and
troubleshooting. The HTTP / WebSocket API reference lives in
[`docs/api/tray.md`](api/tray.md).

---

## 1. Architecture

Every endpoint runs **two** cooperating components:

| Component | Privileges | Lifetime | Role |
| --- | --- | --- | --- |
| **Tray service** | `LocalSystem` (Windows) / `root` (`LaunchDaemon` on macOS) | Long-running, survives reboots and user switching | Holds the persistent connection to MyPortal, performs enrolment, dispatches commands, collects facts, manages auto-update |
| **Tray UI agent** | Logged-in user, per interactive console session | Spawned by the service when a console session appears, terminated on logoff/lock | Renders the tray icon, menus, notifications, and chat window |

```
┌────────────────────┐  WebSocket  ┌────────────────────┐
│   MyPortal server  │◀───────────▶│   Tray service     │  privileged daemon
│  /ws/tray/{uid}    │             │   (SYSTEM/root)    │
└────────────────────┘             └─────────┬──────────┘
                                             │ named pipe / unix socket
                                             ▼
                                  ┌────────────────────┐
                                  │   Tray UI agent    │  per console session
                                  │   (user context)   │
                                  └────────────────────┘
```

### Why split service from UI?

* On Windows, services run in **session 0** which cannot draw UI in the
  active interactive session. Using `CreateProcessAsUser` against the
  active console session token (`WTSGetActiveConsoleSessionId` /
  `WTSQueryUserToken`) gives us a UI process in the user's session.
* On macOS, `LaunchDaemons` cannot show UI; `LaunchAgents` can, and they
  run per-user. The daemon signals the agent via a local socket.
* This separation is also our defence against showing chat windows in
  background (RDP/SSH) sessions — chat is delivered **only** to the
  **active console session**.

---

## 2. Server-side data model

Migration `migrations/235_tray_app.sql` creates four tables and two
companion columns:

* `tray_install_tokens` — short-lived per-company tokens used by the
  installer. Hashed at rest.
* `tray_devices` — one row per enrolled endpoint. Stores the long-lived
  `auth_token` hash, status (`pending` / `active` / `revoked`), facts,
  and last-seen metadata.
* `tray_menu_configs` — menu templates. `scope ∈ {global, company, tag,
  device}`; resolution precedence is **device &gt; tag &gt; company &gt; global**
  (most specific enabled config wins).
* `tray_command_log` — audit trail of commands the server pushes to a
  device (chat invitations, refresh, ping).

Plus:

* `chat_rooms.tray_device_id` (nullable FK) — links a Matrix room to the
  device that originated it.
* `companies.tray_chat_enabled` (boolean) — per-company toggle for
  technician-initiated chats. Default off.

Migrations are applied automatically at startup and are idempotent.

---

## 3. HTTP & WebSocket API

| Path | Method | Auth | Purpose |
| --- | --- | --- | --- |
| `/api/tray/enrol` | POST | Install token in JSON body | Exchange the install token for a per-device `auth_token` |
| `/api/tray/config` | GET | Bearer auth_token | Resolved menu + branding + env-var allowlist + chat toggle |
| `/api/tray/heartbeat` | POST | Bearer auth_token | Liveness ping; updates console user, IP, agent version |
| `/ws/tray/{device_uid}` | WS | Bearer/`X-Tray-Token`/`?token=` | Bidirectional command channel |
| `/api/tray/{device_uid}/chat/start` | POST | Authenticated technician | Create a Matrix room and push `chat_open` |
| `/api/tray/admin/install-tokens` | GET / POST | Super admin | List / create install tokens |
| `/api/tray/admin/install-tokens/{id}/revoke` | POST | Super admin | Revoke an install token |
| `/api/tray/admin/configs` | GET / POST | Super admin | List / create menu configurations |
| `/api/tray/admin/configs/{id}` | PUT / DELETE | Super admin | Update / delete |
| `/api/tray/admin/devices` | GET | Helpdesk technician | List enrolled devices |
| `/api/tray/admin/devices/{id}/revoke` | POST | Super admin | Revoke a device |

All endpoints appear in the Swagger UI at `/docs` per project convention.

### WebSocket protocol (JSON, line-delimited)

**Server → device:**
* `{"type": "ping"}`
* `{"type": "config_changed", "version": <n>}`
* `{"type": "chat_open", "room_id": <int>, "matrix_room_id": "...", "subject": "..."}`
* `{"type": "show_notification", "title": "...", "body": "..."}`
* `{"type": "run_menu_action", "node_id": "..."}` *(only for whitelisted server-defined actions)*

**Device → server:**
* `{"type": "pong"}`
* `{"type": "heartbeat", "console_user": "...", "agent_version": "..."}`
* `{"type": "env_snapshot", "values": {"USERNAME": "..."}}`
* `{"type": "chat_message", "room_id": <int>, "body": "..."}`
* `{"type": "chat_typing", "room_id": <int>}`
* `{"type": "menu_invoked", "node_id": "..."}`
* `{"type": "error", "code": "...", "message": "..."}`

---

## 4. Menu node schema (`payload_json`)

Each entry in `payload_json` is a JSON object:

| `type` | Other fields | Renders as |
| --- | --- | --- |
| `label` | `label` | Static disabled text |
| `link` | `label`, `url` | Opens URL in default browser |
| `submenu` | `label`, `children: [node, …]` | Nested submenu |
| `display_text` | `label`, `text_id` | Opens a small window with the configuration's `display_text` |
| `env_var` | `label`, `name`, `mode: 'show' \| 'copy'` | Reads the env var (must be on the allowlist) and shows / copies the value |
| `open_chat` | `label` | Opens the helpdesk chat window |
| `separator` | — | Horizontal divider |

A built-in default menu is returned when no configuration matches a
device — see `app/services/tray.py::_default_menu()`.

---

## 5. Security model

* **Tokens.** Install tokens and per-device auth tokens are 32-byte
  URL-safe random values. Only their SHA-256 hashes are stored (column
  `*_hash`); a 12-character `*_prefix` is kept for display so admins can
  identify which token is which without exposing the secret.
* **Token rotation.** Calling `/api/tray/enrol` again with the same
  `device_uid` rotates the auth token; the previous token is invalidated.
* **Scoped auth.** Tray tokens authenticate **only** the `/api/tray/*`
  device endpoints and the `/ws/tray/*` socket. They cannot call any
  other API. Admin endpoints require an authenticated user with the
  super-admin (or, for read-only device list, helpdesk-technician) flag.
* **Env-var allowlist.** A device may only request env vars listed in
  the resolved configuration's `env_allowlist`. The server **never**
  receives env values automatically — they are read on the client and
  shown only to the local user.
* **Per-company chat toggle.** `companies.tray_chat_enabled` (default
  `false`) gates technician-initiated chats. Super admins bypass this
  check.
* **CSRF.** All token-bearer endpoints are exempt from session-cookie
  CSRF because they cannot be triggered from a browser cookie context.
* **Rate limiting.** Configure `TRAY_ENROL_RATE_LIMIT` and
  `TRAY_HEARTBEAT_RATE_LIMIT` in `.env`.
* **Sanitisation.** Display text passes through `sanitize_rich_text` on
  save; the same allowlist as the rest of the portal applies.
* **Matrix dependency.** Chat features are gated on `matrix_enabled` —
  when Matrix is disabled, `chat_enabled` is `false` in
  `/api/tray/config` and `chat/start` returns 404.

---

## 6. RMM deployment

### Windows (PowerShell)

```powershell
$url   = 'https://portal.example.com'
$token = 'PASTE-INSTALL-TOKEN-HERE'
Invoke-WebRequest -Uri "$url/static/tray/myportal-tray.msi" -OutFile $env:TEMP\myportal-tray.msi
msiexec.exe /i $env:TEMP\myportal-tray.msi `
    MYPORTAL_URL="$url" ENROL_TOKEN="$token" /qn
```

### macOS (bash)

```bash
URL='https://portal.example.com'
TOKEN='PASTE-INSTALL-TOKEN-HERE'
curl -fsSL "$URL/static/tray/myportal-tray.pkg" -o /tmp/myportal-tray.pkg
sudo /bin/sh -c "
    echo MYPORTAL_URL=$URL  >  /Library/Preferences/io.myportal.tray.env
    echo ENROL_TOKEN=$TOKEN >> /Library/Preferences/io.myportal.tray.env
"
sudo installer -pkg /tmp/myportal-tray.pkg -target /
```

The admin **Install tokens** page generates these snippets pre-filled
with the portal URL and a freshly-minted token (shown once).

### Uninstall

The uninstaller removes the service, agent, registry/plist, and
keychain item. It does **not** call MyPortal — server-side revocation
is a separate explicit action so a wiped device can't silently
re-enrol with the same token. Use the **Devices** admin page to
revoke.

---

## 7. Client (`tray/`)

The Go client lives in the `tray/` folder of this repository (it can
be moved to a sibling repo before GA — see Phase 5 of the rollout
plan). Layout:

```
tray/
├── service/        # Privileged daemon (Windows Service / launchd LaunchDaemon)
├── ui/             # Per-session unprivileged tray UI agent
├── installer/
│   ├── windows/    # WiX/MSI project
│   └── macos/      # pkgbuild/productbuild scripts
├── go.mod
├── Makefile
└── README.md
```

See `tray/README.md` for build instructions.

---

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `/api/tray/enrol` returns 401 | Install token expired / revoked / typo | Generate a new token in admin UI |
| Tray icon never appears after install | UI agent could not be launched in the active console session | Check service logs; ensure the user has logged in interactively at least once |
| "Chat with this device" button missing | Company has `tray_chat_enabled = 0`, or Matrix is disabled | Toggle on the company edit page; verify `MATRIX_ENABLED=true` |
| `env_var` menu node returns "not allowed" | Variable not in the configuration's allowlist | Add it on the configuration editor |
| Devices show as **Unlinked** | Hostname / serial didn't match an existing asset | Manually associate via the Devices admin page (coming in Phase 2.x) |

---

## 9. Rollout phases

This PR delivers Phase 1 (server foundation) plus the Phase 2
chat-start and websocket plumbing. Subsequent PRs cover:

1. ~~Phase 1 — Server foundation~~ ✅
2. ~~Phase 2 — Realtime / chat-start~~ ✅
3. Phase 3 — Windows client MVP (signed MSI, `kardianos/service`,
   `getlantern/systray`, embedded webview)
4. Phase 4 — macOS client MVP (LaunchDaemon + LaunchAgent + signed
   `.pkg`, Developer ID + notarization)
5. Phase 5 — Auto-update, diagnostics upload, RMM packaging,
   load-test the WS hub at ~10k concurrent devices
6. Phase 6 — Branding/theming, per-tag config overrides,
   notifications, localisation scaffolding

---

## 10. Phase 5/6 additions

### Auto-update (`GET /api/tray/version`)

The service polls this endpoint every 6 hours. Response:

```json
{"version": "0.2.0", "download_url": "https://…/myportal-tray.msi", "required": false}
```

Publish a new version from the **Tray > Versions** admin page or via:

```
POST /api/tray/admin/versions
{"version": "0.2.0", "platform": "windows", "download_url": "…", "required": false}
```

### Diagnostics upload (`POST /api/tray/{device_uid}/diagnostics`)

The "Send diagnostics" tray menu item zips the service log directory and
posts the bundle to this endpoint (authenticated, 20 MB cap). Bundles are
stored under `media/tray_diagnostics/` and visible on the **Tray > Diagnostics**
admin page with a download link.

### Push notification (`POST /api/tray/{device_uid}/notify`)

Helpdesk technicians and super-admins can push an OS notification to any
active device:

```json
{"title": "Your ticket is updated", "body": "Ticket #1234 has a new reply."}
```

The notification is delivered immediately if the device's WebSocket is
connected to this app instance; otherwise it is queued in `tray_command_log`
for delivery on the next reconnect (full queued delivery in Phase 5.2).

### Phase 3–4 Go client highlights

- `tray/service/` — Windows Service / macOS LaunchDaemon with
  `github.com/kardianos/service`. Reads config from registry / plist,
  enrolls, keeps WS alive with exponential back-off, forwards commands
  to the UI agent over a local socket.
- `tray/ui/` — systray icon + menu renderer from `payload_json`
  (`label`, `link`, `display_text`, `env_var`, `open_chat`, `separator`,
  `submenu`). Chat window via embedded webview; `nowebview` build tag
  falls back to OS default browser (CGO=0 cross-compile).
- Installers: WiX v7 MSI (Windows) + `pkgbuild`/`productbuild` (macOS).
  RMM deployment scripts: `installer/windows/install.ps1` and
  `installer/macos/install.sh`.
- GitHub Actions workflow: `.github/workflows/tray-build.yml` — build +
  test on every PR; release artifacts on `tray/v*` tags.
