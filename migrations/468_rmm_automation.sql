-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 468: scheduled and onboarding RMM scripts.
--
-- rmm_schedules runs a script on a cron schedule, separate from MyPortal's
-- scheduled tasks. company_id NULL means every company's devices (Common
-- scripts only). target_mode picks the devices: 'all' devices with the RMM
-- agent, devices carrying any of tag_ids_json, or the asset_ids_json chosen.
-- entries_encrypted holds the values a technician entered (they may be
-- secrets); inputs_json is the masked copy shown in the page.
--
-- rmm_onboarding_steps is each company's ordered list of scripts, run one at a
-- time when an RMM agent first enrols. tag_ids_json is the step's filter: it
-- runs only on devices carrying any of those tags (a company tag counts for
-- all its devices) and is skipped on others. rmm_onboarding_runs tracks one
-- device working through that list; plan_json is the list as it was when the
-- run started, with each step's run and outcome.
--
-- rmm_script_runs.run_source says what queued a run: 'manual', 'schedule' or
-- 'onboarding'.

CREATE TABLE IF NOT EXISTS rmm_schedules (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  company_id INT NULL,
  script_id INT NOT NULL,
  cron VARCHAR(128) NOT NULL,
  timezone VARCHAR(64) NOT NULL DEFAULT 'UTC',
  target_mode VARCHAR(16) NOT NULL DEFAULT 'all',
  asset_ids_json LONGTEXT NULL,
  tag_ids_json LONGTEXT NULL,
  entries_encrypted LONGTEXT NULL,
  inputs_json LONGTEXT NULL,
  timeout_seconds INT NOT NULL DEFAULT 600,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  next_run_at DATETIME NULL,
  last_run_at DATETIME NULL,
  last_run_summary VARCHAR(512) NULL,
  created_by_user_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_rmm_schedules_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_schedules_script FOREIGN KEY (script_id) REFERENCES rmm_scripts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_schedules_due ON rmm_schedules (is_enabled, next_run_at);
CREATE INDEX IF NOT EXISTS idx_rmm_schedules_company ON rmm_schedules (company_id);

CREATE TABLE IF NOT EXISTS rmm_onboarding_steps (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  position INT NOT NULL DEFAULT 0,
  script_id INT NOT NULL,
  tag_ids_json LONGTEXT NULL,
  entries_encrypted LONGTEXT NULL,
  inputs_json LONGTEXT NULL,
  timeout_seconds INT NOT NULL DEFAULT 600,
  continue_on_failure TINYINT(1) NOT NULL DEFAULT 0,
  created_by_user_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_rmm_onboarding_steps_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_onboarding_steps_script FOREIGN KEY (script_id) REFERENCES rmm_scripts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_onboarding_steps_company ON rmm_onboarding_steps (company_id, position);

CREATE TABLE IF NOT EXISTS rmm_onboarding_runs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  agent_id INT NULL,
  company_id INT NOT NULL,
  asset_id INT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'running',
  plan_json LONGTEXT NOT NULL,
  current_index INT NOT NULL DEFAULT -1,
  current_run_id INT NULL,
  failed_steps INT NOT NULL DEFAULT 0,
  error_message TEXT NULL,
  started_by_user_id INT NULL,
  started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at DATETIME NULL,
  CONSTRAINT fk_rmm_onboarding_runs_agent FOREIGN KEY (agent_id) REFERENCES rmm_agents(id) ON DELETE SET NULL,
  CONSTRAINT fk_rmm_onboarding_runs_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_onboarding_runs_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_onboarding_runs_status ON rmm_onboarding_runs (status);
CREATE INDEX IF NOT EXISTS idx_rmm_onboarding_runs_company ON rmm_onboarding_runs (company_id, started_at);
CREATE INDEX IF NOT EXISTS idx_rmm_onboarding_runs_agent ON rmm_onboarding_runs (agent_id, status);

ALTER TABLE rmm_script_runs ADD COLUMN IF NOT EXISTS run_source VARCHAR(16) NOT NULL DEFAULT 'manual';
ALTER TABLE rmm_script_runs ADD COLUMN IF NOT EXISTS schedule_id INT NULL;
ALTER TABLE rmm_script_runs ADD COLUMN IF NOT EXISTS onboarding_run_id INT NULL;

CREATE INDEX IF NOT EXISTS idx_rmm_runs_schedule ON rmm_script_runs (schedule_id, agent_id, status);
CREATE INDEX IF NOT EXISTS idx_rmm_runs_onboarding ON rmm_script_runs (onboarding_run_id);
