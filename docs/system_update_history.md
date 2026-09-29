# System update reporting

Scheduled `system_update` tasks always queue `scripts/upgrade.sh --rolling` through
the existing flag processor. Manual update entry points retain their existing
mode selection. The processor records `pending`, `running`, `succeeded`, and
`failed` states, UTC timestamps, bounded coordinator output, and a sanitized
failure summary for global administrators at **Administration → Scheduled tasks
→ System updates**.

## Starting an upgrade from the portal

The System updates page shows the installed and latest version and an **Update
now** button (super administrators only, CSRF protected, audited as
`system.update.request`). The same actions are available to API clients as
`GET /scheduler/system-updates/check` and `POST /scheduler/system-updates`.
Only one update can be queued or running at a time, and a request that has not
started yet can be cancelled.

The web application only records the request; a root job on the host applies
it, so the application never holds root, sudo or the Docker socket:

- **Bare metal**: the request is the same `system_update.flag` the scheduled
  task writes, using the rolling blue/green workflow. The root cron job
  `scripts/process_update_flag.sh` picks it up within a minute.
- **Docker**: the request is written to the app container's state volume and
  picked up by `myportal-docker process-requests` on the host, which upgrades
  to the latest published release. See the Docker guide.

Neither host job takes a target, mode or command from the request other than
the update identifier it reports against, so a compromised application can at
most ask for the upgrade the nightly job would already apply.

While an update runs, the host job publishes the output every few seconds and
the result page refreshes it live. The page keeps polling while the portal
restarts. A request that no host job picks up within 15 minutes is marked
failed with instructions for enabling the host job.

History is stored as mode `0640` JSON files under
`MYPORTAL_SYSTEM_UPDATE_HISTORY_DIR`. In production, leave that setting empty to
use `$MYPORTAL_SHARED_ROOT/state/system-updates`, which survives release
cutovers. For installations without `MYPORTAL_SHARED_ROOT`, set an absolute
persistent path. Back up this directory with the other shared state. Operators
may implement retention by deleting old completed `*.json` files; active
(`pending` or `running`) files should not be removed.

Output is limited to the last 32,000 characters and common credential assignments are
redacted. Update scripts must nevertheless avoid printing credentials. A failed
coordinator still exits non-zero, preserving the existing cron/systemd alerting
and log behavior. Rollback remains the coordinator's automatic restoration of
the previous blue/green upstream; history records the resulting failure output.
