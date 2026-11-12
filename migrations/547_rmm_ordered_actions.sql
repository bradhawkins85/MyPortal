-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 547: several actions per RMM check and event log rule, run in order.
--
-- actions_json holds the ordered list of what a check does when it fails, or
-- what an event log rule does when an event matches: create a ticket, run a
-- script, schedule a script or (checks) install a Chocolatey package, in any
-- combination and as many scripts as needed. Each action has a short key that
-- stays with it when the list is reordered. NULL keeps the single action in the
-- older columns (action, script_id, schedule_mode, ...), which are still
-- written from the first action of each kind so an earlier release keeps
-- working.
--
-- A check or rule can now schedule several scripts, so a scheduled run waits
-- per action as well as per device. Runs scheduled before this migration stay
-- in rmm_check_states.scheduled_at and rmm_event_log_rule_pending and are run
-- as the first scheduled action.

ALTER TABLE rmm_checks ADD COLUMN IF NOT EXISTS actions_json LONGTEXT NULL;
ALTER TABLE rmm_event_log_rules ADD COLUMN IF NOT EXISTS actions_json LONGTEXT NULL;

CREATE TABLE IF NOT EXISTS rmm_check_action_schedules (
  check_id INT NOT NULL,
  agent_id INT NOT NULL,
  action_key VARCHAR(16) NOT NULL,
  run_at DATETIME NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (check_id, agent_id, action_key),
  CONSTRAINT fk_rmm_check_action_schedules_check FOREIGN KEY (check_id) REFERENCES rmm_checks(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_check_action_schedules_due ON rmm_check_action_schedules (run_at);

CREATE TABLE IF NOT EXISTS rmm_event_log_rule_action_schedules (
  rule_id INT NOT NULL,
  agent_id INT NOT NULL,
  action_key VARCHAR(16) NOT NULL,
  run_at DATETIME NOT NULL,
  event_encrypted LONGTEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (rule_id, agent_id, action_key),
  CONSTRAINT fk_rmm_event_log_rule_action_schedules_rule FOREIGN KEY (rule_id) REFERENCES rmm_event_log_rules(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_event_log_rule_action_schedules_due ON rmm_event_log_rule_action_schedules (run_at);
