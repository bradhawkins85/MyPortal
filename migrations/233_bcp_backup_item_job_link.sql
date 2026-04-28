-- Link bcp_backup_item rows to backup_jobs so that jobs created in
-- /admin/backup-jobs automatically appear in /bcp/backups.
--
-- The column is nullable so existing manual backup items are unaffected.
-- ON DELETE CASCADE ensures that when a backup job is removed, its
-- corresponding BCP backup item is also removed automatically.
-- The unique key prevents a job from being linked to the same plan twice.

ALTER TABLE bcp_backup_item
  ADD COLUMN IF NOT EXISTS backup_job_id INT NULL AFTER plan_id,
  ADD CONSTRAINT fk_bcp_backup_item_job
      FOREIGN KEY (backup_job_id) REFERENCES backup_jobs (id) ON DELETE CASCADE;

-- Prevent duplicate entries for the same (plan, backup job) pair
CREATE UNIQUE INDEX IF NOT EXISTS uq_bcp_backup_item_job
    ON bcp_backup_item (plan_id, backup_job_id);
