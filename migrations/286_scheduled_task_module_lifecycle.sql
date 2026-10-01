ALTER TABLE scheduled_tasks
  ADD COLUMN IF NOT EXISTS disabled_by_module VARCHAR(100) NULL;

CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_disabled_by_module
  ON scheduled_tasks (disabled_by_module);
