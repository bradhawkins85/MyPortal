-- Distributed singleton-job lease table.
-- Used by app/services/singleton_jobs.py so APScheduler tasks fire on
-- only one MyPortal instance even when two instances run behind nginx.
CREATE TABLE IF NOT EXISTS singleton_jobs (
    job_name VARCHAR(191) NOT NULL PRIMARY KEY,
    owner_id VARCHAR(191) NOT NULL,
    expires_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_singleton_jobs_expires
    ON singleton_jobs (expires_at);
