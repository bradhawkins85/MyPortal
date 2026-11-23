# Discovered Device Port Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a technician check arbitrary TCP ports on one discovered device, via the scanner agent that found it, and see the result in a modal and on the device's row.

**Architecture:** Portal stores one latest check per device in `network_device_port_checks`, sends a `check_ports` tray command (queued if offline), and the Go tray service does TCP connects and posts the result to a tray-authenticated endpoint. The devices page polls a JSON endpoint from a modal.

**Tech Stack:** FastAPI, async MySQL/SQLite (`app.core.database.db`), Jinja2, vanilla JS, Go 1.25 tray service.

**Spec:** `docs/superpowers/specs/2026-11-23-discovered-device-port-check-design.md`

## Global Constraints

- TCP only; at most **1,024** ports per check; ports **1–65535**.
- Agent connect timeout **2 s**, at most **64** parallel connects.
- Agent refuses IPs outside `NetworkScanLocalCIDRs` and when scanning is disabled.
- Only the latest check per device is kept.
- Permission: `menu.network_devices` write (`can_configure`); company-scoped.
- Offline agent → command `queued`, drained by the existing reconnect queue.
- Closed and filtered are both shown as **Closed / no answer**.
- SQL uses bound parameters only; no f-string SQL in new code.
- Timestamps stored UTC, rendered with `<span data-utc>`.

## Review Focus

1. Port specs like `100-50`, `0`, `65536`, `80-`, `1-65535`, duplicates/overlaps → reversed ranges and out-of-range values rejected with a plain 422; overlaps deduplicated; >1,024 rejected. (Task 1)
2. A result posted by a different tray device, or for a check replaced by a newer one → 404, nothing stored. (Task 4)
3. Reported open ports not among the requested ports → 422. (Task 4)
4. Scanner disabled between queueing and delivery → agent reports `failed` with a reason instead of scanning. (Task 5)
5. Device of another company, or device whose scanner is no longer enabled → 404 from the user route. (Task 3)

---

### Task 1: Port spec parsing

**Files:** Create `app/services/port_checks.py`; Test `tests/test_port_checks.py`

**Interfaces — Produces:**
- `MAX_PORTS = 1024`
- `parse_port_spec(raw: str) -> list[int]` — sorted unique; raises `ValueError` with a user-facing message.
- `format_port_spec(ports: Iterable[int]) -> str` — compresses runs: `[443, 8000, 8001, 8002] -> "443,8000-8002"`.

- [ ] Tests: `"443, 8000-8002 22"` → `[22, 443, 8000, 8001, 8002]`; `"80,80,79-81"` → `[79, 80, 81]`; `ValueError` for `""`, `"0"`, `"65536"`, `"100-50"`, `"80-"`, `"abc"`, `"1-2000"` (over cap); `format_port_spec` round-trips.
- [ ] Run `pytest tests/test_port_checks.py -q` → FAIL; implement; → PASS; commit.

### Task 2: Migration and repository

**Files:** Create migration via `/new-migration` (`network_device_port_checks`); Modify `app/repositories/network_devices.py`; Test `tests/test_network_device_port_checks_repo.py`

Table columns per spec; `id INT PK AUTO_INCREMENT`, FK-free (indexes on `network_device_id`, `scanner_tray_device_id`), `status VARCHAR(20)`, `ports TEXT`, `open_ports TEXT`, `checked_count INT`, `error TEXT`, `requested_at DATETIME`, `completed_at DATETIME NULL`. Must run on SQLite fallback.

**Interfaces — Produces (all in `network_devices`):**
- `get_device_for_port_check(company_id, device_id) -> dict | None` — row with `ip_address`, `hostname`, `scanner_tray_device_id`, `scanner_device_uid`, `scanner_enabled`, `scanner_name`.
- `create_port_check(*, company_id, network_device_id, scanner_tray_device_id, ip_address, ports: str, requested_by_user_id) -> int` — deletes the device's older checks first.
- `set_port_check_command(check_id, command_id, status) -> None`
- `get_latest_port_check(company_id, network_device_id) -> dict | None`
- `latest_port_checks_by_device(company_id) -> dict[int, dict]`
- `get_port_check_for_scanner(check_id, scanner_tray_device_id) -> dict | None`
- `complete_port_check(check_id, *, open_ports: str, checked_count: int, error: str | None) -> None` — status `completed` or `failed`.

- [ ] Tests (mock `db`): `create_port_check` issues DELETE then INSERT with bound params; `get_port_check_for_scanner` filters on both ids; `complete_port_check` sets `failed` when `error`.
- [ ] Fail → implement → pass; commit.

### Task 3: User routes

**Files:** Modify `app/features/assets/routes.py` (after `scan_network_now`); Test `tests/test_network_device_port_check_routes.py`

**Interfaces — Consumes:** Task 1, Task 2, `tray_service.send_to_device`, `tray_repo.log_command`, `tray_repo.mark_command_delivered`.
**Produces:**
- `POST /devices/discovered/{device_id}/port-check` (form `ports`) → JSON `{"check": {...}, "delivered": bool}` when `Accept: application/json`, else flash redirect to `/devices#discovered-device-{id}`. 422 JSON `{"detail": msg}` on bad spec. Command payload: `{"type": "check_ports", "command_id", "check_id", "ip_address", "ports": [int]}`.
- `GET /devices/discovered/{device_id}/port-check` → `{"check": {...} | null}`; check dict keys `id, status, ports, open_ports (list[int]), checked_count, error, requested_at, completed_at` (ISO UTC).
- `network_devices_page` adds `port_checks` (from `latest_port_checks_by_device`) to the template context.

- [ ] Tests: write access required (403 for read-only); unknown/other-company device or disabled scanner → 404; bad spec → 422 and nothing created; delivered → status `sent`, command marked delivered; offline → `queued`, `delivered: false`; GET returns serialised check.
- [ ] Fail → implement → pass; commit.

### Task 4: Tray result endpoint

**Files:** Modify `app/api/routes/tray.py`, `app/schemas/tray.py`; Test `tests/test_tray_port_check_result.py`

**Produces:** `POST /api/tray/network-scan/port-checks/{check_id}` (tray auth, `summary="Report a discovered device port check"`), body `PortCheckResultRequest{command_id: int, open_ports: list[int] = [], checked_count: int = 0, error: str | None (max 500)}` → `{"status": "ok"}`. 404 if `get_port_check_for_scanner` misses or `command_id` mismatches; 422 if any open port not in the check's ports. Calls `complete_port_check` and `tray_repo.mark_command_completed(command_id, error=...)`. Also document `check_ports` in the `app/schemas/tray.py` command docstring.

- [ ] Tests for success, wrong device (404), stale command id (404), unrequested port (422), error result → `failed`.
- [ ] Fail → implement → pass; commit.

### Task 5: Agent

**Files:** Create `tray/internal/scanner/portcheck.go` (+ `_test.go`); Modify `tray/internal/api/client.go`, `tray/service/main.go`; Test `tray/service/reporting_test.go`

**Produces:**
- `scanner.MaxPortCheckPorts = 1024`
- `scanner.CheckPorts(ctx context.Context, ip string, ports []int, timeout time.Duration, workers int) []int` — sorted open ports.
- `(*api.Client).PostPortCheckResult(ctx, checkID, commandID int, openPorts []int, checked int, errMsg string) error`
- `daemon.handleCheckPorts(msg map[string]json.RawMessage)`, dispatched from `case "check_ports"`; validates ids, IP via `scanner.IPAllowed(ip, cfg.NetworkScanLocalCIDRs)` and scanner enabled (fresh `GetConfig`), port count/range; reports failures with a plain message.

- [ ] Tests: `CheckPorts` finds a `net.Listen` port open and a closed port closed; `handleCheckPorts` against httptest portal posts `failed` when scanning is disabled and when IP is outside the local ranges, and posts the open listener port on success.
- [ ] `go test ./...` in `tray/` → pass; commit.

### Task 6: UI

**Files:** Modify `app/templates/devices/index.html`, `app/static/js/devices.js`, `app/static/css/app.css` (`.port-check-*`); Test extend `tests/test_network_scanners_page.py`

- Actions menu: `<form method="post" action="/devices/discovered/{{ device.id }}/port-check" data-port-check-form>` with a hidden-by-JS fallback `ports` input, and a `button` with `data-port-check-open="{{ device.id }}"`, `aria-haspopup="dialog"`, `aria-controls="port-check-modal"`.
- Modal `#port-check-modal` per spec copy; live summary `[data-port-check-summary]`, inline error `[data-port-check-error]`, results list `[data-port-check-results]` with `role="status" aria-live="polite"`. Client-side parse mirrors Task 1 rules.
- Polls `GET …/port-check` every 2 s, stops at `completed`/`failed` or after 2 minutes.
- Row chip in the Open ports cell from `port_checks[device.id]`.

- [ ] Template test asserts the action, modal id, data hooks and chip markup; `node --check app/static/js/devices.js`.
- [ ] Commit.

### Task 7: Housekeeping

- [ ] Bump `app/features/assets/__init__.py` `version` to `1.26.0`; change log via `/changelog-entry`; SQL audit of touched files; run all new tests plus `tests/test_network_scanners_page.py`; commit and push.
