-- Allow noisy high-frequency scheduled tasks to be hidden from the cron calendar.
ALTER TABLE scheduled_tasks
  ADD COLUMN IF NOT EXISTS exclude_from_calendar TINYINT(1) NOT NULL DEFAULT 0;
