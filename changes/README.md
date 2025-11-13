# Change History

This directory is used to store change log entries as JSON files. Each file represents a single change entry with metadata including timestamp, type, and summary.

## Purge Notice

All historical change log records were purged as part of a one-time cleanup operation. This included:

- **4,181 JSON change log files** from this directory
- **2 markdown files** (changes.md and change.md) from the repository root
- **Database records** from the `change_log` table

## Database Cleanup

If you have an active MyPortal deployment with a database, you should also truncate the `change_log` table to complete the purge:

```sql
TRUNCATE TABLE change_log;
```

Alternatively, you can run the purge script with database support:

```bash
python scripts/purge_change_history.py
```

## Future Change Logs

Moving forward, new change log entries will be added to this directory following the standard format defined in `app/services/change_log.py`.
