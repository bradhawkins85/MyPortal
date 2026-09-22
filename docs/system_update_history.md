# System update reporting

Scheduled `system_update` tasks always queue `scripts/upgrade.sh --rolling` through
the existing flag processor. Manual update entry points retain their existing
mode selection. The processor records `pending`, `running`, `succeeded`, and
`failed` states, UTC timestamps, bounded coordinator output, and a sanitized
failure summary for global administrators at **Administration → Scheduled tasks
→ Update history**.

History is stored as mode `0640` JSON files under
`MYPORTAL_SYSTEM_UPDATE_HISTORY_DIR`. In production, leave that setting empty to
use `$MYPORTAL_SHARED_ROOT/state/system-updates`, which survives release
cutovers. For installations without `MYPORTAL_SHARED_ROOT`, set an absolute
persistent path. Back up this directory with the other shared state. Operators
may implement retention by deleting old completed `*.json` files; active
(`pending` or `running`) files should not be removed.

Output is limited to 32,000 characters and common credential assignments are
redacted. Update scripts must nevertheless avoid printing credentials. A failed
coordinator still exits non-zero, preserving the existing cron/systemd alerting
and log behavior. Rollback remains the coordinator's automatic restoration of
the previous blue/green upstream; history records the resulting failure output.
