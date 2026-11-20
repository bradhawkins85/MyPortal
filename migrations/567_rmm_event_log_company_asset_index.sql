-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- database: event-log
-- Use EVENT_LOG_DB_* (including its configured database name), not DB_*.
-- The asset page reads a device's newest entries. Startup only tries to add
-- this index in the background and gives up behind a busy table, so large
-- installations went without it and MariaDB walked the whole company's
-- history newest first for devices that stopped reporting (504 on the asset
-- page). Built online so agents keep writing while it builds.
-- IF NOT EXISTS also handles installs where startup already added it.
ALTER TABLE rmm_event_log_entries
    ADD INDEX IF NOT EXISTS idx_rmm_event_log_company_asset_time
        (company_id, asset_id, time_created),
    ALGORITHM=INPLACE,
    LOCK=NONE;
