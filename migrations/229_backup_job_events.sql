-- Backup History: per-day status events for a backup job.
-- A scheduled task seeds an "unknown" event for every active job at
-- the start of each day; the public webhook upserts the event status
-- (pass / fail / warn / unknown / etc.) when the backup script reports.

CREATE TABLE IF NOT EXISTS backup_job_events (
  id INT AUTO_INCREMENT PRIMARY KEY,
  backup_job_id INT NOT NULL,
  event_date DATE NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'unknown',
  status_message TEXT NULL,
  reported_at DATETIME NULL,
  source VARCHAR(64) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_backup_job_events_job FOREIGN KEY (backup_job_id) REFERENCES backup_jobs(id) ON DELETE CASCADE,
  UNIQUE KEY uq_backup_job_events_job_date (backup_job_id, event_date),
  INDEX idx_backup_job_events_status (status),
  INDEX idx_backup_job_events_date (event_date)
);
