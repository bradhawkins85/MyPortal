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
