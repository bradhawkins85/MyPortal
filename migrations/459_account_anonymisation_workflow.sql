-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Account anonymisation admin workflow (issue #4554).
--
-- Adds the approval columns to account_anonymisation_requests and moves the
-- statuses written by the first release (executing/failed/cancelled) onto the
-- pending/approved/rejected/completed lifecycle. A failed run stays approved
-- so it can be retried. The hash index lets marketing campaigns exclude
-- anonymised addresses. Safe to re-run.
ALTER TABLE account_anonymisation_requests ADD COLUMN IF NOT EXISTS decided_by INT NULL;
ALTER TABLE account_anonymisation_requests ADD COLUMN IF NOT EXISTS decided_at DATETIME NULL;
ALTER TABLE account_anonymisation_requests ADD COLUMN IF NOT EXISTS completed_at DATETIME NULL;
ALTER TABLE account_anonymisation_requests ADD COLUMN IF NOT EXISTS notes TEXT NULL;

UPDATE account_anonymisation_requests
SET status = 'approved'
WHERE status IN ('executing', 'failed');

UPDATE account_anonymisation_requests
SET status = 'rejected'
WHERE status = 'cancelled';

UPDATE account_anonymisation_requests
SET completed_at = executed_at
WHERE status = 'completed' AND completed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_account_anonymisation_requests_email_hash
    ON account_anonymisation_requests (original_email_hash);
