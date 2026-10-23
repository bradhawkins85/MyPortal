-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 518: script result rules.
--
-- A rule watches runs of one Gitea script (script_id) or of any script
-- (script_id NULL) as they finish, and compares the run's output, error
-- output, exit code, result and returned values (conditions_json: a list of
-- {"field", "name", "operator", "value"}). match_mode 'all' needs every
-- condition to match, 'any' at least one. When it matches it either raises a
-- ticket for the device (action 'create_ticket'; ticket_mode 'update' adds
-- later matches to the ticket while it is open, 'new' raises one each time)
-- or runs another script on the same device (action 'run_script',
-- next_script_id), optionally handing it the result as MYPORTAL_PREVIOUS_*
-- environment variables (pass_results). Script values are kept encrypted as
-- for event log rules.
--
-- Scope: company_id NULL applies to every company (super admins only).
--
-- rmm_script_runs.parent_run_id and result_rule_id record which run and rule
-- queued a follow-up script, so chains can be traced and kept short.

CREATE TABLE IF NOT EXISTS rmm_script_result_rules (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  company_id INT NULL,
  script_id INT NULL,
  match_mode VARCHAR(8) NOT NULL DEFAULT 'all',
  conditions_json TEXT NOT NULL,
  action VARCHAR(16) NOT NULL,
  next_script_id INT NULL,
  entries_encrypted LONGTEXT NULL,
  inputs_json LONGTEXT NULL,
  timeout_seconds INT NOT NULL DEFAULT 600,
  pass_results TINYINT(1) NOT NULL DEFAULT 1,
  ticket_mode VARCHAR(8) NOT NULL DEFAULT 'update',
  ticket_subject VARCHAR(255) NULL,
  ticket_priority VARCHAR(16) NOT NULL DEFAULT 'normal',
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  match_count BIGINT NOT NULL DEFAULT 0,
  last_matched_at DATETIME NULL,
  last_run_summary VARCHAR(512) NULL,
  created_by_user_id INT NULL,
  updated_by_user_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_rmm_script_result_rules_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_script_result_rules_script FOREIGN KEY (script_id) REFERENCES rmm_scripts(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_script_result_rules_next FOREIGN KEY (next_script_id) REFERENCES rmm_scripts(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_script_result_rules_company ON rmm_script_result_rules (company_id);
CREATE INDEX IF NOT EXISTS idx_rmm_script_result_rules_script ON rmm_script_result_rules (script_id);

CREATE TABLE IF NOT EXISTS rmm_script_result_rule_tickets (
  rule_id INT NOT NULL,
  agent_id INT NOT NULL,
  ticket_id INT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (rule_id, agent_id),
  CONSTRAINT fk_rmm_script_result_rule_tickets_rule FOREIGN KEY (rule_id) REFERENCES rmm_script_result_rules(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE rmm_script_runs ADD COLUMN IF NOT EXISTS parent_run_id INT NULL;
ALTER TABLE rmm_script_runs ADD COLUMN IF NOT EXISTS result_rule_id INT NULL;
