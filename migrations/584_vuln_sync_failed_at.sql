-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 584: when the vulnerability sync last failed. A failed run never
-- records nvd_synced_at, so without this the scheduler restarted the sync every
-- minute; the sync now waits before retrying after a failure.

ALTER TABLE vuln_sync_state ADD COLUMN IF NOT EXISTS failed_at DATETIME NULL;
