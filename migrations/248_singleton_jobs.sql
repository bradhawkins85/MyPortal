-- Distributed singleton-job lease table.
-- Used by app/services/singleton_jobs.py so APScheduler tasks fire on
-- only one MyPortal instance even when two instances run behind nginx.
--
-- All timestamps stored UTC (project convention; see
-- docs/feature_packs.md and docs/zero_downtime_upgrades.md).  The lease
-- helper writes naive ``datetime.now(timezone.utc)`` values formatted
-- as ``YYYY-MM-DD HH:MM:SS``.
CREATE TABLE IF NOT EXISTS singleton_jobs (
    job_name VARCHAR(191) NOT NULL PRIMARY KEY,
    owner_id VARCHAR(191) NOT NULL,
    expires_at DATETIME NOT NULL COMMENT 'UTC',
    updated_at DATETIME NOT NULL COMMENT 'UTC'
);

CREATE INDEX IF NOT EXISTS idx_singleton_jobs_expires
    ON singleton_jobs (expires_at);
