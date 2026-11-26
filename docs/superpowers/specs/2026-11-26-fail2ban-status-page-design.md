# Fail2ban status page — design

## Why

Admins had no way to see from MyPortal whether Fail2ban protection was
working. A misconfigured log setting stopped every event reaching
`auth.log` and nobody noticed until bans stopped happening. Admins want one
page that answers "is it working?" and "who is blocked right now?".

## Goals

- Super-admin-only page at `/admin/fail2ban` (Administration sidebar).
- Health checks first: is the Fail2ban log being written, is the ban
  snapshot current, are events recording real visitor addresses
  (`TRUSTED_PROXIES`), is each MyPortal jail running. Every problem says how
  to fix it in plain English.
- Each MyPortal jail (`myportal-auth`, `myportal-honeypot`,
  `myportal-probe`, plus any other `myportal-*` jail): running state, failed
  and banned counts, and its rules in words.
- *Blocked now*: every banned address with jail, ban time, unban time and
  the reason (the last logged event for that address).
- *Recent activity* from `FAIL2BAN_LOG_PATH`: counts by event type, top
  addresses and the latest events over the last 24 hours.

## Non-goals and anti-goals

- **View only.** No unblock, ban or jail control from the web. The page
  shows the `fail2ban-client unban` command to run on the server instead.
- Never give the web service (user `myportal`, `NoNewPrivileges`) access to
  Fail2ban's root-only socket or database, and add no sudo rule.
- No country/WHOIS lookups or external calls.

## Approaches considered

1. **Root snapshot + MyPortal's log (chosen).** A root systemd timer runs
   `scripts/fail2ban_snapshot.py` every minute. It asks `fail2ban-client`
   about MyPortal jails and writes a JSON snapshot that `myportal` can read
   but not change. The page combines it with the tail of `auth.log`.
   Trade-off: the ban list is up to a minute old.
2. **Read `/var/log/fail2ban.log`** via the `adm` group. Rejected: `adm`
   reads every system log, and current bans can't be derived reliably from
   a rotated log.
3. **MyPortal's log only.** Rejected: cannot show actual bans.

## Design

### Root snapshot job

- `scripts/fail2ban_snapshot.py`, Python stdlib only, run as
  `python3 -I` by `deploy/systemd/myportal-fail2ban-snapshot.service`
  (oneshot, root, hardened) from the current release, on
  `myportal-fail2ban-snapshot.timer` (every minute).
- Commands (absolute `fail2ban-client` path, 10 s timeout, no shell):
  `status`, `status <jail>`, `get <jail> banip --with-time` (falls back to
  the status ban list on Fail2ban 0.10), `get <jail> bantime|findtime|maxretry|bantime.increment|bantime.maxtime`.
- Ban times from `--with-time` are server-local; the script converts them
  to UTC ISO 8601.
- Output `/var/lib/myportal-fail2ban/status.json`. The directory must be a
  real root-owned directory (mode 0750, group `myportal`); the file is
  written to a temp file in that directory, `fchmod 0640`, `fchown
  root:myportal`, then `os.replace`. A root job never writes into a
  directory the web user controls (symlink attacks).
- Fail2ban missing or stopped still writes a snapshot with
  `available: false` / `running: false` and the error, so the page can
  explain it. Bans per jail are capped at 5 000 entries (`truncated: true`).
- Installed and enabled by `scripts/upgrade.sh` on every deploy and by
  `scripts/install_environment.sh` on first install, like the backup timer.

### Portal side

- New feature pack `app/features/fail2ban/` (routes, handlers, service).
  `GET`/`HEAD /admin/fail2ban`, `_require_super_admin_page`, `no-store`.
- Setting `FAIL2BAN_STATUS_PATH` (default
  `/var/lib/myportal-fail2ban/status.json`, blank disables) in
  `app/core/config.py` and `.env.example`.
- Service reads the snapshot (stale after 5 minutes) and the last 4 MB of
  `FAIL2BAN_LOG_PATH` plus the newest rotated file when the window starts
  before the current file, in a worker thread. It parses `AUTH LOGIN FAIL`,
  `AUTH LOGIN SUCCESS`, `HONEYPOT TRAP` and `HONEYPOT PROBE` lines.
- Proxy check: at least 10 events in the window and over half from
  loopback → warn to set `TRUSTED_PROXIES`.
- Template `admin/fail2ban.html` with `.f2b-*` styles in
  `app/static/css/fail2ban.css`; tables use `data_table`/`table_toolbar`,
  timestamps use `data-utc`. Sidebar entry for super admins, default
  Administration group, and `/admin/fail2ban` added to the trusted-network
  restricted paths like other admin tools.

### Left to the builder

Exact copy, CSS, table column widths, and top-N sizes.
