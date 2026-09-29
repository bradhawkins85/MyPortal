# System update reporting

Scheduled `system_update` tasks always queue `scripts/upgrade.sh --rolling` through
the existing flag processor. Manual update entry points retain their existing
mode selection. The processor records `pending`, `running`, `succeeded`, and
`failed` states, UTC timestamps, bounded coordinator output, and a sanitized
failure summary for global administrators at **Administration → System
Updates** in the left menu.

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

On bare metal the shared state directory is writable by the `myportal`
service account, so the root job treats everything in it as untrusted: its
lock, the deployment plan and the captured upgrade output live in the
root-only `/var/lib/myportal-updater` (mode `0700`, created by the installer
and by `upgrade.sh` when missing), the flag is read once without following
symlinks, and files the application reads (`system_update.status`,
`feature_pack_reload.flag`) are published as `root:myportal 0640` with
`install`, which replaces a planted symlink rather than writing through it.
History records are written by `scripts/system_update_report.py` running as
the service account (`runuser -u myportal`), with the output streamed on
standard input, exactly as the Docker host job does inside its container. The
cron job logs to the root-owned `/var/log/myportal-updater.log`; `upgrade.sh`
migrates existing cron entries that still log under `/var/log/myportal`.

History is stored as mode `0600` JSON files under
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
