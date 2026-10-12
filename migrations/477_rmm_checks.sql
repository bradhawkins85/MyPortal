-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 477: RMM checks.
--
-- A check watches one thing on a device: disk space, a ping target, CPU load,
-- free memory, a Windows service, network throughput, a script's result or
-- whether the agent has stopped checking in (check_type). Like event log rules
-- a check applies to every company (company_id and asset_id NULL), one company
-- or one device. For the same thing (check_type and target, such as disk "C:")
-- a device's own check replaces its company's, which replaces one for every
-- company; a replacing check that is off switches the thing off for its scope.
--
-- settings_json holds the type's own settings (thresholds, the expected
-- service state, what a script must return). duration_minutes is how long the
-- problem must last before the check fails. When it fails the check takes the
-- same actions as an event log rule: run a script (at most once per
-- cooldown_minutes on a device) or raise a ticket for the device (one open
-- ticket per check and device). A script check's own script and values are in
-- check_script_id, check_entries_encrypted and check_inputs_json.
--
-- rmm_check_states keeps each check's result per device (agent).

CREATE TABLE IF NOT EXISTS rmm_checks (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  company_id INT NULL,
  asset_id INT NULL,
  check_type VARCHAR(16) NOT NULL,
  target VARCHAR(255) NOT NULL DEFAULT '',
  settings_json TEXT NULL,
  duration_minutes INT NOT NULL DEFAULT 0,
  check_script_id INT NULL,
  check_entries_encrypted LONGTEXT NULL,
  check_inputs_json LONGTEXT NULL,
  action VARCHAR(16) NOT NULL DEFAULT 'none',
  script_id INT NULL,
  entries_encrypted LONGTEXT NULL,
  inputs_json LONGTEXT NULL,
  timeout_seconds INT NOT NULL DEFAULT 600,
  cooldown_minutes INT NOT NULL DEFAULT 60,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  last_failed_at DATETIME NULL,
  last_summary VARCHAR(512) NULL,
  created_by_user_id INT NULL,
  updated_by_user_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_rmm_checks_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_checks_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_checks_check_script FOREIGN KEY (check_script_id) REFERENCES rmm_scripts(id) ON DELETE SET NULL,
  CONSTRAINT fk_rmm_checks_script FOREIGN KEY (script_id) REFERENCES rmm_scripts(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_checks_company ON rmm_checks (company_id);
CREATE INDEX IF NOT EXISTS idx_rmm_checks_asset ON rmm_checks (asset_id);
CREATE INDEX IF NOT EXISTS idx_rmm_checks_type ON rmm_checks (check_type);

CREATE TABLE IF NOT EXISTS rmm_check_states (
  check_id INT NOT NULL,
  agent_id INT NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  value_text VARCHAR(512) NULL,
  problem_since DATETIME NULL,
  checked_at DATETIME NULL,
  changed_at DATETIME NULL,
  last_action_at DATETIME NULL,
  ticket_id INT NULL,
  run_id INT NULL,
  next_run_at DATETIME NULL,
  PRIMARY KEY (check_id, agent_id),
  CONSTRAINT fk_rmm_check_states_check FOREIGN KEY (check_id) REFERENCES rmm_checks(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_check_states_run ON rmm_check_states (run_id);
CREATE INDEX IF NOT EXISTS idx_rmm_check_states_agent ON rmm_check_states (agent_id);
