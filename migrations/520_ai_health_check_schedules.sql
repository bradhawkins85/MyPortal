-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 520: monthly health checks and health check remediation scripts.
--
-- ai_health_checks.run_source says what started a check: 'manual' (a
-- technician or the API) or 'scheduled' (the monthly check).
--
-- ai_health_check_schedules keeps one row per asset with an RMM agent: when
-- its monthly health check is next due (next_run_at, a random time in the
-- calendar month that ends at window_ends_at) and how the last one went
-- (app.services.ai_health_check_schedule).
--
-- ai_health_check_groups chooses which computers get monthly checks: a
-- computer with the RMM agent is checked when it matches any enabled group.
-- A group applies to one company, or to every company (company_id NULL,
-- super admins), and narrows it with the same nested AND/OR device rules as
-- RMM checks (target_rules_json; NULL means every device in scope). The
-- default 'All computers' group keeps every computer checked until admins
-- narrow or turn it off.
--
-- ai_health_check_remediations links a script to one of the health check's
-- automatic detections (detection_key, see
-- app.services.health_check_detections); the script runs on the device when a
-- health check finds it (app.services.health_check_remediation). company_id
-- NULL applies to every company (Common scripts only, super admins). A
-- company's own link, on or off, replaces it for that company.
-- scope_company_id is company_id, or 0 for every company, so a detection has
-- one link per scope. Script values are kept encrypted as for event log rules.

ALTER TABLE ai_health_checks ADD COLUMN IF NOT EXISTS run_source VARCHAR(16) NOT NULL DEFAULT 'manual';

CREATE TABLE IF NOT EXISTS ai_health_check_schedules (
  asset_id INT NOT NULL PRIMARY KEY,
  company_id INT NOT NULL,
  next_run_at DATETIME NOT NULL,
  window_ends_at DATETIME NOT NULL,
  last_started_at DATETIME NULL,
  last_health_check_id INT NULL,
  last_result VARCHAR(512) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_ai_health_check_schedules_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE,
  CONSTRAINT fk_ai_health_check_schedules_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_ai_health_check_schedules_due ON ai_health_check_schedules (next_run_at);

CREATE TABLE IF NOT EXISTS ai_health_check_groups (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  company_id INT NULL,
  target_rules_json LONGTEXT NULL,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  is_default TINYINT(1) NOT NULL DEFAULT 0,
  created_by_user_id INT NULL,
  updated_by_user_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_ai_health_check_groups_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_ai_health_check_groups_company ON ai_health_check_groups (company_id);

INSERT INTO ai_health_check_groups (name, company_id, target_rules_json, is_enabled, is_default)
SELECT 'All computers', NULL, NULL, 1, 1
WHERE NOT EXISTS (SELECT 1 FROM ai_health_check_groups WHERE is_default = 1);

CREATE TABLE IF NOT EXISTS ai_health_check_remediations (
  id INT AUTO_INCREMENT PRIMARY KEY,
  detection_key VARCHAR(64) NOT NULL,
  company_id INT NULL,
  scope_company_id INT NOT NULL DEFAULT 0,
  script_id INT NULL,
  entries_encrypted LONGTEXT NULL,
  inputs_json LONGTEXT NULL,
  timeout_seconds INT NOT NULL DEFAULT 600,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  run_count INT NOT NULL DEFAULT 0,
  last_run_at DATETIME NULL,
  last_summary VARCHAR(512) NULL,
  created_by_user_id INT NULL,
  updated_by_user_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_ai_health_check_remediations_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_ai_health_check_remediations_script FOREIGN KEY (script_id) REFERENCES rmm_scripts(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_health_check_remediations_scope
  ON ai_health_check_remediations (detection_key, scope_company_id);
