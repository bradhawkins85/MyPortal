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
| `/ws/tray/{device_uid}` | WS | Bearer/`X-Tray-Token` header | Bidirectional command channel |
| `/api/tray/{device_uid}/chat/start` | POST | Authenticated technician | Create a Matrix room and push `chat_open` |
| `/api/tray/admin/install-tokens` | GET / POST | Super admin | List / create install tokens |
| `/api/tray/admin/install-tokens/{id}/revoke` | POST | Super admin | Revoke an install token |
| `/api/tray/admin/deployment-links` | GET / POST | Super admin | List / create deployment URLs |
| `/api/tray/admin/deployment-links/{id}/revoke` | POST | Super admin | Revoke a deployment URL and its install token |
| `/deploy/{slug}` | GET | Deployment URL slug | Public download page for one company |
| `/deploy/{slug}/windows.exe`, `/windows.msi` | GET | Deployment URL slug | Company-specific signed Windows installers |
| `/deploy/{slug}/macos.zip` | GET | Deployment URL slug | pkg plus its settings file |
| `/api/tray/admin/deployment-links/{id}/rebuild` | POST | Super admin | Queue a new Windows installer build |
| `/api/tray/build-agent/jobs/claim` | POST | API key | Build agent claims the next build |
| `/api/tray/build-agent/jobs/{id}/artifacts/{msi\|exe}` | PUT | API key | Build agent uploads an installer (raw body) |
| `/api/tray/build-agent/jobs/{id}/complete`, `/fail` | POST | API key | Build agent finishes or reports a failure |
| `/deploy/{slug}/install.ps1`, `/install.sh` | GET | Deployment URL slug | RMM install script with the token filled in |
| `/api/tray/admin/configs` | GET / POST | Super admin | List / create menu configurations |
| `/api/tray/admin/configs/{id}` | PUT / DELETE | Super admin | Update / delete |
| `/api/tray/admin/devices` | GET | Helpdesk technician | List enrolled devices |
| `/api/tray/admin/devices/{id}/revoke` | POST | Super admin | Revoke a device |
| `/api/tray/{device_uid}/troubleshoot` | POST | Helpdesk / super admin | Ask an active device to run the AI troubleshooting agent for a ticket (queues + pushes a `troubleshoot` WS command) |
| `/api/tickets/{ticket_id}/troubleshoot-complete` | POST | Device bearer | AI agent reports generated guidance + sanitized log bundle for a ticket (multipart) |

All endpoints appear in the Swagger UI at `/docs` per project convention.

### WebSocket protocol (JSON, line-delimited)

**Server → device:**
* `{"type": "ping"}`
* `{"type": "config_changed", "version": <n>}`
* `{"type": "chat_open", "room_id": <int>, "matrix_room_id": "...", "subject": "..."}`
* `{"type": "show_notification", "title": "...", "body": "..."}`
* `{"type": "run_menu_action", "node_id": "..."}` *(only for whitelisted server-defined actions)*
* `{"type": "troubleshoot", "ticket_id": <int>, "command_id": <int>, "prompt": "...", "llm_base_url": "...", "llm_model": "...", "llm_api_key": "..."}` *(AI troubleshooting: the device collects sanitized local logs, asks a local LLM for guidance, then POSTs the result back to `troubleshoot-complete`. Fields are flat top-level JSON.)*
* `{"type": "troubleshoot", "ticket_id": <int>, "command_id": <int>, "mode": "collect_logs", "log_requests": [{"source": "System", "reason": "...", "hours": 24}]}` *(Multi-stage troubleshooter: the device collects only the allowlisted log sources named, scrubs them and uploads them to `troubleshoot-complete`; the server analyses them. No LLM credentials are sent.)*

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
| `env_var` | `label`, `name` | Reads the env var (must be on the allowlist) and shows the value in a popup |
| `open_chat` | `label` | Opens the helpdesk chat window |
| `separator` | — | Horizontal divider |

A built-in default menu is returned when no configuration matches a
device — see `app/services/tray.py::_default_menu()`.

### 4.1 Company-conditional menu nodes

Any menu node can include company visibility conditions. The server evaluates
these conditions against the company assigned to the tray device before sending
the menu payload to the agent. This lets admins expose a TRMM script, link,
submenu, or any other node only to the companies where it applies.

| Field | Type | Description |
| --- | --- | --- |
| `visible_company_ids` | array of integers | Optional allowlist. When present, the node is shown only when the device's `company_id` is in this list. |
| `hidden_company_ids` | array of integers | Optional denylist. When present, the node is hidden when the device's `company_id` is in this list. |

Example TRMM script node visible only for company IDs `12` and `34`:

```json
{
  "type": "TRMM_Script",
  "label": "Run onboarding script",
  "script_id": 123,
  "script_name": "Onboarding",
  "visible_company_ids": [12, 34]
}
```

The **Admin → Tray → Configurations** menu-node editor exposes these as
**Show only for companies** and **Hide for companies** dropdown fields on
every row. Empty submenus are removed after their children are filtered out.

---

### 4.2 Displaying an environment variable in the tray menu

Follow these steps to add a menu item that shows the value of a Windows or macOS environment variable when a user clicks it.

**Step 1 — Identify the variable name**

Decide which environment variable you want to surface. Common examples:

| Variable | Platform | Typical value |
| --- | --- | --- |
| `USERNAME` | Windows | `jsmith` |
| `USERDOMAIN` | Windows | `CORP` |
| `COMPUTERNAME` | Windows | `LAPTOP-001` |
| `USER` | macOS | `jsmith` |
| `HOSTNAME` | macOS | `jsmith-mbp` |

The variable name is **case-insensitive** — `username` and `USERNAME` are treated the same way.

**Step 2 — Add the variable to the configuration allowlist**

The tray client will only read variables that are explicitly listed in the resolved configuration's allowlist. Values are read on the client device and are **never** sent to the server.

1. Go to **Admin → Tray → Configurations** and open (or create) the configuration you want to edit.
2. In the **Environment variable allowlist** field, enter the variable names as a comma-separated list, for example:

   ```
   USERNAME,USERDOMAIN,COMPUTERNAME
   ```

3. Save the configuration.

**Step 3 — Add an `env_var` menu row**

In the same configuration, use the **Menu nodes** list editor and add a row with type `env_var`. Populate the required fields:

| Field | Type | Description |
| --- | --- | --- |
| `type` | string | Must be `"env_var"` |
| `name` | string | Exact environment variable name, e.g. `"USERNAME"` |
| `label` | string | Menu item text shown to the user |

Example values for an `env_var` row:

```json
Type: `env_var`  
Name: `USERNAME`  
Label: `Logged-in user`

If you need advanced nesting, you can still use the **Advanced JSON** toggle to edit raw JSON directly.
```

**Step 4 — Save and verify**

1. Click **Save configuration** in the admin UI.
2. The server broadcasts a `config_changed` WebSocket message to all devices that match this configuration's scope. The tray client reloads its menu automatically within a few seconds.
3. Right-click the tray icon on the target device. You should see the menu items you added.
4. Click a **"Logged-in user"** (or equivalent) item — a small popup window displays the current value of that variable. If the variable is not set on the device, the popup shows `(not set)`.

**Troubleshooting**

| Symptom | Cause | Fix |
| --- | --- | --- |
| Menu item shows `(not set)` | The variable is not in the user's environment | Confirm the variable exists by running `echo %USERNAME%` (Windows) or `echo $USERNAME` (macOS) in a terminal logged in as that user |
| Menu item does not appear at all | The variable name is not in the allowlist | Re-check the **Environment variable allowlist** field in the configuration — names must match exactly (the check is case-insensitive) |
| Menu does not refresh | Device is offline or WebSocket is disconnected | The client re-fetches config on reconnect; restart the tray service if the issue persists |

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

### Deployment URLs

**Admin → Tray → Deployment URLs** (`/admin/tray/deployment-links`) creates a
private magic link for one company. Whoever opens it gets a page with
*Download for Windows* and *Download for macOS* buttons, so no token needs to
be typed or pasted. The company-specific packages are only ever served by
MyPortal through that link; nothing is published to GitHub, and the public
release installers (MSI, pkg, DMG) are built exactly as before.

* **Windows** downloads a signed `setup.exe`, with the matching `.msi` linked
  for IT teams. Both are built for that company by the Windows build agent,
  with `MYPORTAL_URL` and `ENROL_TOKEN` baked in as MSI property defaults (a
  command-line value still overrides them). Until a build server is set up, or
  while a build is running, the page says the Windows installer is being
  prepared. See [Tray Build Server](Tray%20Build%20Server.md).
* **macOS** downloads a zip with the cached `myportal-tray.pkg`, its
  `myportal-tray.env` settings and `Install MyPortal Tray.command`, which
  writes the settings and runs the pkg after asking for the Mac password.
* **RMM tools** can use the one-liners shown on the admin page:
  `irm '<link>/install.ps1' | iex` or `curl -fsSL '<link>/install.sh' | sudo bash`.

Each link owns its own company install token, and both expire together after
the period chosen when the link is created (1, 7, 30 or 90 days, default 7).
Once a link expires or is revoked its page stops working, and any package
already downloaded from it can no longer enrol new devices. Revoking also
deletes the link's built installers. Devices already enrolled are unaffected.
The link is a credential: the slug is stored as an HMAC hash for lookup and
encrypted so admins can copy it again, and every `/deploy` response is sent
with `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.

### Tactical RMM ticket URL Action

MyPortal can open its ticket form directly from a Tactical RMM agent and link
the resulting ticket to that agent's synced asset. First import the Tactical
RMM assets so that the `TrayAgentID` agent custom field contains the enrolled
MyPortal tray device UID. Then add a URL Action under **Settings → Global
Settings → URL Actions** with this pattern (replace the hostname):

```text
https://portal.example.com/api/tray/ticket-form/url-action?TrayAgentID={{agent.TrayAgentID}}
```

The variable name is case-sensitive. Tactical RMM URL-encodes the custom-field
value. MyPortal accepts only an active tray device that is linked to an asset,
then redirects the browser to a short-lived encrypted MyPortal ticket form.
The URL Action never exposes the internal asset or device database ID.

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

---

## 11. AI Troubleshooting Agent

### Multi-stage troubleshooter (ticket assets)

The 🤖 button on an asset in a ticket's asset list runs the troubleshooter
in stages, each posted to the ticket as an internal note:

1. **Articles** - the server asks the troubleshooter LLM for published
   troubleshooting articles matching the ticket and searches the internal
   knowledge base (public articles plus the ticket's company only).
2. **Recommended steps** - the LLM turns those articles into ordered steps
   and says which endpoint logs, if any, would confirm or rule out a cause,
   with a reason for each. Sources are limited to a fixed allowlist of
   Windows Event Log channels and macOS unified-log subsystems
   (`tray/internal/agent/logsources.go`, mirrored in
   `app/services/ai_troubleshooter.py`). Log files the articles tell the
   technician to check (for example `CBS.log`, `setupapi.dev.log`,
   `NetSetup.LOG`, `IntuneManagementExtension.log`, `Report.wer`, or a macOS
   crash report) are requested as `file:<path>`; a `*` may match file or
   folder names, and `%TEMP%`, `%ProgramData%`, `%SystemRoot%`, `%AppData%`
   and `%LocalAppData%` are expanded. A file must be inside one of the
   allowlisted log folders (`tray/internal/agent/logfiles.go`) and end in `.log`, `.txt`,
   `.wer`, `.lo_`, `.ips`, `.crash`, `.panic` or `.diag`. The device reads
   at most 10 files per request (newest first; a wildcard only reads files
   changed in the requested window) and the last 1 MB of each.

   The built-in folders cover Windows and the common Microsoft applications:

   | Area | Folders |
   | --- | --- |
   | Windows | `C:\Windows\Logs`, `C:\Windows\Panther`, `C:\Windows\debug`, `C:\Windows\INF`, `C:\Windows\Temp`, `C:\Windows\Security\Logs`, `C:\Windows\System32\LogFiles`, `C:\Windows\SoftwareDistribution` |
   | Management and security | `C:\Windows\CCM\Logs`, `C:\ProgramData\Microsoft\IntuneManagementExtension\Logs`, `C:\ProgramData\Microsoft\Windows Defender\Support`, the two WER report folders, `C:\inetpub\logs\LogFiles` |
   | Office and Outlook | `%LocalAppData%\Microsoft\Office\Logs`, `%LocalAppData%\Microsoft\Olk` (new Outlook), `%LocalAppData%\Temp` (Click-to-Run setup and Outlook Logging), `C:\Windows\Temp` |
   | Teams | `%AppData%\Microsoft\Teams` (classic), the `MSTeams_8wekyb3d8bbwe` package Logs folder (new Teams), `%LocalAppData%\Microsoft\Teams`, `%LocalAppData%\Microsoft\TeamsMeetingAddin` |
   | OneDrive and Edge | `%LocalAppData%\Microsoft\OneDrive\logs`, `%LocalAppData%\Microsoft\OneDrive\setup\logs`, `C:\ProgramData\Microsoft OneDrive\setup\logs`, `C:\ProgramData\Microsoft\EdgeUpdate\Log` |
   | macOS | `/var/log`, `/Library/Logs`, `~/Library/Logs` (AutoUpdate, OneDrive, Office), `~/Library/Containers/*/Data/Library/Logs` (sandboxed Office apps), the new Teams container Logs folder, `~/Library/Application Support/Microsoft/Teams` |

   Owners can allow more folders with `TROUBLESHOOT_LOG_FOLDERS_WINDOWS` and
   `TROUBLESHOOT_LOG_FOLDERS_MACOS` (separated by `;`, for example
   `C:\ProgramData\Vendor\Logs;C:\Users\*\AppData\Local\Vendor\Logs`).
   They are added to the built-in list, sent to the device with the request
   and re-checked there; a folder must name at least two real folders, so a
   drive or `C:\Users\*` on its own is ignored.
3. **Log collection** - when logs are needed, the server sends a
   `collect_logs` troubleshoot command to the asset's tray device. The
   device re-checks every source against its own allowlist, collects and
   scrubs the logs, and uploads them to `troubleshoot-complete`. The bundle
   is a `.tar.gz` with one file per log source (for example `System.log`,
   `Microsoft-Windows-WLAN-AutoConfig_Operational.log`), attached to the
   ticket as a staff-only file. On Windows the channels are read with the
   built-in `wevtutil qe` (one line per event: time, level, event ID,
   provider and message); a source that cannot be read keeps its own file
   with the reason. Each collected log file is its own file in the bundle,
   named after its path.
4. **Log analysis** - the server sends the logs to the LLM along with the
   reason each was collected and the recommended steps, and posts the
   findings, likely causes and possible solutions.

**Optional public web search.** Set `TROUBLESHOOT_WEB_SEARCH_ENABLED=true`
to also search the public web in stage 1 (off by default). Choose
`TROUBLESHOOT_WEB_SEARCH_PROVIDER=searxng` with your SearXNG instance in
`TROUBLESHOOT_WEB_SEARCH_URL` (JSON output enabled), or `brave` with a Brave
Search API key in `TROUBLESHOOT_WEB_SEARCH_API_KEY`.
`TROUBLESHOOT_WEB_SEARCH_MAX_PAGES` (1-5, default 3) caps how many result
pages are read. Only the generic search terms the AI writes are sent to the
provider, never ticket text. Pages are fetched with the outbound URL guard
(no private, loopback or cloud-metadata addresses). The steps found on each
page are posted as an extra note with its URL, ready to turn into an internal
knowledge base article.

Steps 1, 2 and 4 run on the server, so the LLM credentials never leave it.
Older tray agents ignore `mode` and upload their default log set (System,
Application and Security), which the server still analyses. Each run is
listed as "AI troubleshooter" on the LLM Usage page.

### Single-stage agent (API)

When an endpoint is being troubleshot, a technician can ask the device to
collect its own logs and produce an **AI-assisted diagnosis**. This is a
fully client-side pipeline: the device gathers recent logs, sanitises them,
asks a **local LLM** for guidance, and posts the result back to the server —
raw log data is never uploaded unsanitised.

### How it works

1. A technician (or super-admin) calls `POST /api/tray/{device_uid}/troubleshoot`
   with the `ticket_id` and a natural-language `prompt`. The server resolves
   the configured LLM, queues the job in `tray_command_log`, and pushes a
   `troubleshoot` command over the device WebSocket (see §3). The command
   carries the `ticket_id` / `command_id` identifiers, the `prompt`, and the
   local LLM endpoint (`llm_base_url` / `llm_model` / `llm_api_key`).
2. The service runs the Go agent (`tray/internal/agent/`) in a background
   goroutine: it collects up to 24 h of platform logs (Windows Event Log
   + common app logs on Windows; `log` / `Console` subsystem logs and
   common app logs on macOS), scrubs secrets, and truncates to a safe
   sample.
3. It prompts the LLM for **actionable guidance** (the LLM never executes
   anything; the response is advisory text).
4. The result — `guidance`, the `endpoint` hostname, and the sanitized
   `log_bundle` (a redacted `.tar.gz` with one file per log source, sent
   as a multipart form file) — is posted to
   `POST /api/tickets/{ticket_id}/troubleshoot-complete`.
5. The server verifies the command, attaches the bundle as a read-only
   (staff-only) ticket attachment, and stores the guidance as an internal
   ticket note.

If the LLM is unavailable the bundle is still uploaded (degraded result).
If no logs can be collected, a bundle is not sent.

### CLI

A standalone CLI wraps the same agent for one-off / development use:

```sh
# from tray/
make run-agent ARGS="-prompt 'WiFi keeps dropping' -llm-base-url http://127.0.0.1:11434 -llm-model llama3"
```

Flags: `-prompt`, `-endpoint`, `-llm-base-url`, `-llm-model`, `-llm-api-key`,
`-window` (default `24h`), `-out` (write the `.tar.gz` bundle to a file), and
`-no-llm` (skip the LLM, bundle-only). Guidance is printed to stdout; the
log bundle is written to `-out` when provided (otherwise the CLI only
reports its size). Ctrl-C cancels an in-flight LLM call.

### Makefile targets

| Target | Purpose |
| --- | --- |
| `make build-agent` | Build `dist/agent/tray-troubleshoot` (CGO=0, cross-compiles) |
| `make test-agent` | Run the `internal/agent/` unit tests |
| `make run-agent ARGS="…"` | Build + run the CLI with the given flags |
