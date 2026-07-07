-- Track when a ticket entered its current status for automation filters and list columns.
ALTER TABLE tickets
    ADD COLUMN IF NOT EXISTS status_changed_at DATETIME(6) NULL AFTER status;

UPDATE tickets
SET status_changed_at = COALESCE(status_changed_at, updated_at, created_at)
WHERE status_changed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_tickets_status_changed_at
    ON tickets (status, status_changed_at);
