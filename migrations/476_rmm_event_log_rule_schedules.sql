-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 476: event log rules that schedule a script.
--
-- A rule with action 'schedule_script' runs its script on the device later
-- instead of straight away: schedule_mode 'delay' waits delay_minutes,
-- 'time' waits for the next run_time (HH:MM) in schedule_timezone.
-- rmm_event_log_rule_pending holds the one pending run per rule and device
-- (a second match while one is pending is not stacked); the RMM automation
-- loop queues it when run_at passes. event_encrypted keeps the matching
-- event for the script's {{event.*}} values.

ALTER TABLE rmm_event_log_rules ADD COLUMN IF NOT EXISTS schedule_mode VARCHAR(8) NULL;
ALTER TABLE rmm_event_log_rules ADD COLUMN IF NOT EXISTS delay_minutes INT NULL;
ALTER TABLE rmm_event_log_rules ADD COLUMN IF NOT EXISTS run_time VARCHAR(5) NULL;
ALTER TABLE rmm_event_log_rules ADD COLUMN IF NOT EXISTS schedule_timezone VARCHAR(64) NULL;

CREATE TABLE IF NOT EXISTS rmm_event_log_rule_pending (
  rule_id INT NOT NULL,
  agent_id INT NOT NULL,
  run_at DATETIME NOT NULL,
  event_encrypted LONGTEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (rule_id, agent_id),
  CONSTRAINT fk_rmm_event_log_rule_pending_rule FOREIGN KEY (rule_id) REFERENCES rmm_event_log_rules(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_event_log_rule_pending_due ON rmm_event_log_rule_pending (run_at);
