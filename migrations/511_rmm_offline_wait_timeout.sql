-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Queue deadlines are independent from execution deadlines. NULL overrides inherit the script setting.
ALTER TABLE rmm_scripts ADD COLUMN IF NOT EXISTS default_queue_timeout_seconds INT NOT NULL DEFAULT 0;
ALTER TABLE rmm_schedules ADD COLUMN IF NOT EXISTS queue_timeout_seconds INT NULL;
ALTER TABLE rmm_onboarding_steps ADD COLUMN IF NOT EXISTS queue_timeout_seconds INT NULL;
ALTER TABLE rmm_script_runs ADD COLUMN IF NOT EXISTS queue_timeout_seconds INT NULL;

-- Undo execution timeouts of zero that could have inherited the removed global Forever setting.
UPDATE rmm_scripts SET default_timeout_seconds = 600 WHERE default_timeout_seconds = 0;
UPDATE rmm_script_runs SET timeout_seconds = 600 WHERE timeout_seconds = 0;
UPDATE rmm_schedules SET timeout_seconds = 600 WHERE timeout_seconds = 0;
UPDATE rmm_onboarding_steps SET timeout_seconds = 600 WHERE timeout_seconds = 0;
UPDATE rmm_checks SET timeout_seconds = 600 WHERE timeout_seconds = 0;
UPDATE rmm_event_log_rules SET timeout_seconds = 600 WHERE timeout_seconds = 0;
