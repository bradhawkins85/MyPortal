# Discovered device port check — design

## Intent

From the **Discovered devices** list (`/devices`), a technician wants to check
once whether a service is up on a device, on ports outside what routine scans
cover, and then move on. Routine scans probe a fixed set (15 ports on the
Windows PowerShell scanner, Nmap's default top 1,000 elsewhere); there is no
way today to ask "is 3306 / 8000-8100 open on this box right now?".

## Goals

- Pick a device, enter a port, a range or a mix (`443, 8000-8100`) and have the
  scanner agent that discovered it check those TCP ports.
- See the answer in a focused modal as soon as the agent reports, and keep the
  latest answer visible on the device's row so closing the modal loses nothing.

## Non-goals

- Changing routine scans or the device's **Open ports** column.
- Remembering custom ports per device or re-checking them on a schedule.
- UDP checks, service/version detection, or checking hosts that are not
  already discovered devices.

## Anti-goals

- Turning a scanner into a route to arbitrary networks: the agent must refuse
  any IP outside its configured local ranges, as routine scans do.
- A check that silently never happens: offline agents get the check queued and
  the user is told so.

## Approaches considered

- **A. Live one-off check in a modal, latest result kept per device — chosen.**
  Dedicated `check_ports` agent command, Go-native TCP connects, result posted
  back and shown in the modal and as a chip on the row.
- B. Fire-and-forget: result only in the agent command log. Rejected — the user
  has to go hunting for the answer, which defeats the point.
- C. Merge into **Open ports**. Rejected — the next routine scan overwrites it.

## Design

### Portal — data

New table `network_device_port_checks` (migration via `/new-migration`):
`id`, `company_id`, `network_device_id`, `scanner_tray_device_id`,
`command_id` (tray_command_log id), `ip_address`, `ports` (normalised spec,
e.g. `443,8000-8100`), `status` (`queued` | `sent` | `completed` | `failed`),
`open_ports` (comma list), `checked_count`, `error`, `requested_by_user_id`,
`requested_at`, `completed_at` (UTC). Only the latest check per device is kept:
creating a check deletes that device's older rows.

### Portal — user routes (`app/features/assets/routes.py`)

- `POST /devices/discovered/{id}/port-check` — session auth, CSRF,
  `menu.network_devices` write (the existing `can_configure`). Device must
  belong to the active company and its scanner must still be enabled.
  Parses the ports spec (comma/space separated ports and `a-b` ranges,
  1–65535, deduplicated, at most 1,024 ports; inline 422 message otherwise).
  Logs a `check_ports` tray command, sends it, marks the check `sent` if
  delivered, else leaves it `queued` (drained on reconnect by the existing
  queue). Returns JSON for the modal; a non-JS form post gets a flash redirect.
- `GET /devices/discovered/{id}/port-check` — latest check as JSON for polling.

### Agent (`tray/service`)

- `dispatchWSMessage` gets `case "check_ports"`. Payload: `command_id`,
  `check_id`, `ip_address`, `ports` (ints).
- Re-fetches config; refuses (reports `failed` with a plain reason) when
  scanning is disabled, the IP is outside `NetworkScanLocalCIDRs`, or more than
  1,024 ports were sent.
- TCP connect to each port with `net.DialTimeout` (2 s), at most 64 in
  parallel. Same code on Windows, macOS and Linux; no Nmap.
- Posts `{command_id, open_ports, checked_count, error}` to
  `POST /api/tray/network-scan/port-checks/{check_id}`.

### Portal — agent result endpoint (`app/api/routes/tray.py`)

Tray-device auth. The check must belong to the calling device and match
`command_id`; reported ports must be a subset of those requested. Stores the
result, marks the command completed (or error), returns 204/200.

### UI (`app/templates/devices/index.html`, `app/static/js/devices.js`)

- **Check ports…** in each device's Actions menu (when `can_configure`), with
  `aria-haspopup="dialog"`.
- `<div class="modal" role="dialog" hidden>` editor. Header: eyebrow with the
  device's hostname/IP, title *Check ports*, subtitle *Checked from <scanner>*.
  Body: ports input with help text ("One port, a range like 8000-8100, or a
  mix separated by commas. Up to 1,024 ports.") and a live summary
  ("12 ports: 443, 8000–8010") with inline validation. Footer: *Cancel* and
  primary *Check ports*.
- After submit the modal shows *Waiting for the scanner…* (or *The scanner is
  offline — the check will run when it reconnects*), polls every 2 s for up to
  2 minutes, then lists every checked port as `status--success` **Open** or
  `status--neutral` **Closed / no answer**. After 2 minutes it says the result
  will appear on the device's row.
- Row: under the Open ports cell, a chip like *Port check: 2 of 12 open ·
  <span data-utc>…</span>* (or *Port check waiting*), from the latest check.
- Without JS, the action is a real form post and the row chip shows the result
  after reload.

### Housekeeping

Bump the `assets` feature pack minor version, change log entry, Swagger
summaries on the new endpoints, pytest for spec parsing, permission/company
scoping and result-endpoint ownership, Go tests for payload validation and the
dial loop against a local listener. Audit SQL in touched files.

## Calls I made (correct any)

- Offline agent → check is queued (matches the existing command queue) rather
  than refused.
- Only the latest check per device is kept.
- 2 s connect timeout, 64 parallel connects, 1,024-port cap.
- "Closed" and "filtered" are reported together as **Closed / no answer**.
