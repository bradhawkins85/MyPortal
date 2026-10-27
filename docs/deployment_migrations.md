# Deployment migrations

Production workers never migrate at startup. `scripts/upgrade.sh` invokes
`manage.py migrate` once, under the database advisory lock, before starting or
cutting traffic to a new immutable release. Development and test environments
may opt in with `MIGRATION_BOOTSTRAP_ON_START=true`; production rejects it.

Every new SQL file begins with deployment metadata:

```sql
-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
```

Migrations with `-- database: event-log` run on a dedicated connection using
`EVENT_LOG_DB_*`, including a custom database name or remote host. They share
the secondary database's startup index lock and run synchronously before
cutover; failures are recorded in the main `migrations` ledger and stop the
upgrade. If event logging is unconfigured, these files remain pending for a
later migration run. They never fall back to the main database. Secondary
migrations must be idempotent because DDL can commit before ledger completion.
Migration 528 requires UPG01 maintenance mode and permanently truncates
`rmm_event_log_entries` before adding the repeat-event index, avoiding a build
over the collected history. It retains shared message text and collection
settings. The truncation runs even if the index already exists. Keep event-log
collection stopped until migration completion; retrying after partial DDL
truncates the entries again. The index addition skips an existing index.

The production upgrade coordinator automatically checks pending migrations
using `manage.py migration-maintenance-required`. When maintenance is required,
it publishes the maintenance state, stops both application slots and any
legacy application service, and passes `--maintenance` to the schema phase.
It clears maintenance only after the new release passes readiness and cutover.
If migration or cutover fails, workers remain paused and maintenance stays
enabled; fix the reported error and rerun the upgrade. A persistent updater
marker keeps retries in maintenance even when the schema phase already
completed. Ordinary migrations keep the rolling upgrade path.

The coordinator executes deployment scripts from the fetched target commit,
staged in the root-only updater directory. Local edits or a non-main control
branch do not force it to use stale deployment logic. The control checkout
and protected environment file remain authoritative for repository access and
configuration; local checkout files are not overwritten by this handoff.
If an older installed updater still reports the maintenance-mode error,
refresh its `scripts/upgrade.sh` from `origin/main` once (retain a backup of any
local edits), then retry System Updates. Updating application releases alone
does not replace the root cron coordinator in an un-updatable control checkout.

`expand` adds backwards-compatible schema, `data`/`backfill` changes data while
both releases remain compatible, and `contract` removes or changes an old
contract. An online contract must name compatible serving and target release
IDs and is deployed only after the earlier expand release has been serving for
one full release. Otherwise declare `maintenance: true` and enable UPG01
maintenance mode. Only universally compatible expand-only releases skip the
worker reload.

Released migrations are immutable because their checksums may already be in a
customer database. The companion `migrations/deployment_metadata.json` records
metadata that was missing from migrations in the maintained pre-UPG02 upgrade
window. It is consulted only when a SQL file has no inline metadata; new
migrations must continue to use the inline header. Migration 380 requires
UPG01 maintenance because it backfills a GUID and then makes that column
mandatory, so an older serving release could otherwise insert an invalid row
between those operations.
Maintenance-only metadata does not block a fresh empty-database bootstrap,
because no older worker can be serving that schema.

The `migrations` table is the operational record: checksum, running/completed/
failed state, phase, timestamps, duration, and a bounded error are durable.
Applied-file checksum drift and failures stop deployment before cutover. MySQL
DDL may autocommit before a later statement fails; operators must compare the
failed file with the live schema, make its statements idempotent or repair the
schema, and retry the explicit migration phase. Never delete the failure row.

Rollback switches traffic only to an application revision declared compatible
with the already-migrated schema. Deployments do not run automatic down SQL.
If no retained revision is compatible, keep UPG01 maintenance enabled and
roll forward with a repair/compatibility release.
