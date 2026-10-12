-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 474: event log rules.
--
-- A rule matches incoming RMM event log entries on their fields
-- (conditions_json: a list of {"field", "operator", "value"}, all of which must
-- match) and either drops them before they are stored (action 'drop') or runs
-- an RMM script on the device that sent them (action 'run_script').
--
-- Scope: company_id and asset_id both NULL applies to every company; a
-- company_id alone to that company's devices; an asset_id to one device.
-- Script values are kept encrypted as for schedules (entries_encrypted) with
-- the masked copy shown in run history (inputs_json). cooldown_minutes stops a
-- burst of events running the script again on the same device;
-- rmm_event_log_rule_triggers remembers when it last ran on each device.

CREATE TABLE IF NOT EXISTS rmm_event_log_rules (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  company_id INT NULL,
  asset_id INT NULL,
  action VARCHAR(16) NOT NULL,
  conditions_json TEXT NOT NULL,
  script_id INT NULL,
  entries_encrypted LONGTEXT NULL,
  inputs_json LONGTEXT NULL,
  timeout_seconds INT NOT NULL DEFAULT 600,
  cooldown_minutes INT NOT NULL DEFAULT 60,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  match_count BIGINT NOT NULL DEFAULT 0,
  last_matched_at DATETIME NULL,
  last_run_summary VARCHAR(512) NULL,
  created_by_user_id INT NULL,
  updated_by_user_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_rmm_event_log_rules_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_event_log_rules_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_event_log_rules_script FOREIGN KEY (script_id) REFERENCES rmm_scripts(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_event_log_rules_company ON rmm_event_log_rules (company_id);
CREATE INDEX IF NOT EXISTS idx_rmm_event_log_rules_asset ON rmm_event_log_rules (asset_id);

CREATE TABLE IF NOT EXISTS rmm_event_log_rule_triggers (
  rule_id INT NOT NULL,
  agent_id INT NOT NULL,
  last_run_id INT NULL,
  last_triggered_at DATETIME NOT NULL,
  PRIMARY KEY (rule_id, agent_id),
  CONSTRAINT fk_rmm_event_log_rule_triggers_rule FOREIGN KEY (rule_id) REFERENCES rmm_event_log_rules(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
