---
name: new-migration
description: Create a new SQL migration in migrations/ with the next free number and the required deployment metadata header. Use whenever a change needs a schema or data migration.
argument-hint: <short_snake_case_name>
---

# New migration

1. Get the number (checks this branch and `origin/main`, so parallel branches
   don't collide):

   ```bash
   python3 .claude/skills/new-migration/next_number.py
   ```

2. Create `migrations/<number>_<short_snake_case_name>.sql`. The leading comment
   block is required; the runner rejects new migrations without a valid phase:

   ```sql
   -- phase: expand
   -- compatible-from: *
   -- compatible-to: *
   -- maintenance: false
   -- <Why this migration exists, in a sentence or two.>
   ```

   - `phase`: `expand` (backwards-compatible schema), `data` / `backfill`
     (data changes while both releases stay compatible), or `contract`
     (removes or changes an old contract).
   - A `contract` migration needs explicit `compatible-from` / `compatible-to`
     releases, or `maintenance: true`.
   - `maintenance: true` only when the change cannot run online (UPG01).
   - Add `-- database: event-log` only for tables in the event-log database.
   - See `docs/deployment_migrations.md` for the full policy.

3. Make the SQL idempotent: `CREATE TABLE IF NOT EXISTS`,
   `ADD COLUMN IF NOT EXISTS`, `ADD INDEX IF NOT EXISTS`, `INSERT IGNORE`, etc.
   It must also run on the SQLite fallback.

4. Never edit a migration that has already been released; add a new one.

5. Add a regression test next to the existing ones
   (`tests/test_*_migration.py`), e.g. assert the file exists and contains the
   key statements.

6. If main gains the same number before merge, rename the file to the next
   free number (rerun step 1) and update any test that references it.
