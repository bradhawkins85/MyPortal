-- Add per-job alert threshold columns to backup_jobs.
-- A NULL or 0 value means no alert is raised for that status.

ALTER TABLE backup_jobs
  ADD COLUMN IF NOT EXISTS alert_no_success_days INT NULL DEFAULT NULL COMMENT 'Alert if no successful backup in this many days (NULL/0 = disabled)',
  ADD COLUMN IF NOT EXISTS alert_fail_days        INT NULL DEFAULT NULL COMMENT 'Alert if backup has failed for this many consecutive days (NULL/0 = disabled)',
  ADD COLUMN IF NOT EXISTS alert_unknown_days     INT NULL DEFAULT NULL COMMENT 'Alert if backup status has been unknown for this many days (NULL/0 = disabled)';
