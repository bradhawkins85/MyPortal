-- phase: data
-- compatible-from: *
-- compatible-to: *
-- maintenance: true
-- database: event-log
-- Use EVENT_LOG_DB_* (including its configured database name), not DB_*.
-- Repeat-event lookups filter by agent, message and creation time. Without
-- this index they can repeatedly scan large per-agent event histories.
-- Discard collected entry history first so large installations do not need
-- to index hundreds of millions of disposable rows. This is irreversible.
-- Shared message text and collection settings are retained. A retry after
-- partial DDL truncates entries again; keep collection stopped in maintenance.
-- IF NOT EXISTS also handles installs repaired manually or by startup DDL.
TRUNCATE TABLE rmm_event_log_entries;

ALTER TABLE rmm_event_log_entries
    ADD INDEX IF NOT EXISTS idx_rmm_event_log_repeats
        (agent_id, message_id, time_created),
    ALGORITHM=INPLACE,
    LOCK=NONE;
