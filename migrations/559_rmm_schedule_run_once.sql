-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- RMM schedules can run a script one time instead of on a repeating cron
-- schedule. run_once_at (UTC) holds that time; cron is left empty for those
-- schedules, and the schedule turns itself off once it has run.
ALTER TABLE rmm_schedules ADD COLUMN IF NOT EXISTS run_once_at DATETIME NULL;
