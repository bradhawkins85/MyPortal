-- Add pass_protection flag to backup_jobs.
-- When enabled, a job that has already recorded a "pass" status for the
-- current day cannot be downgraded to "warn" or "fail" by a subsequent
-- API call.  This is useful for jobs that run multiple times per day
-- against unreliable storage: once a successful backup is confirmed that
-- day the status is locked as passed.

ALTER TABLE backup_jobs
  ADD COLUMN IF NOT EXISTS pass_protection TINYINT(1) NOT NULL DEFAULT 0
    COMMENT 'When 1, a Pass status for the day cannot be overwritten by Warn or Fail';
