-- Add support for one-time scheduling in automations
-- This allows automations to run once at a specific time and not repeat

-- Add scheduled_time column for one-time scheduling
ALTER TABLE automations 
ADD COLUMN scheduled_time DATETIME(6) NULL AFTER next_run_at;

-- Add run_once flag to indicate if automation should run only once
ALTER TABLE automations 
ADD COLUMN run_once TINYINT(1) NOT NULL DEFAULT 0 AFTER scheduled_time;

-- Add index for querying one-time scheduled automations
CREATE INDEX idx_automations_scheduled_time ON automations(scheduled_time);
