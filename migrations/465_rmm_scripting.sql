-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 465: RMM scripting.
--
-- rmm_scripts holds scripts loaded from the Gitea script repository, with the
-- parameters and environment variables detected in each one. rmm_agents are
-- the separate RMM agents enrolled on devices. rmm_script_runs records every
-- script pushed to an agent: what the technician entered, the resolved values
-- (encrypted, and cleared when the run finishes), and the exit code, output and
-- custom values the script returned.

CREATE TABLE IF NOT EXISTS rmm_scripts (
  id INT AUTO_INCREMENT PRIMARY KEY,
  path VARCHAR(512) NOT NULL,
  name VARCHAR(255) NOT NULL,
  folder VARCHAR(512) NOT NULL DEFAULT '',
  language VARCHAR(16) NOT NULL,
  description TEXT NULL,
  content LONGTEXT NOT NULL,
  content_sha256 CHAR(64) NOT NULL,
  source_sha VARCHAR(64) NULL,
  parameters_json LONGTEXT NULL,
  env_vars_json LONGTEXT NULL,
  default_timeout_seconds INT NOT NULL DEFAULT 600,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  synced_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_rmm_scripts_path UNIQUE (path)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rmm_agents (
  id INT AUTO_INCREMENT PRIMARY KEY,
  agent_uid VARCHAR(64) NOT NULL,
  tray_device_id INT NULL,
  company_id INT NULL,
  asset_id INT NULL,
  auth_token_hash VARCHAR(128) NOT NULL,
  auth_token_prefix VARCHAR(16) NOT NULL,
  hostname VARCHAR(255) NULL,
  os VARCHAR(32) NULL,
  os_version VARCHAR(128) NULL,
  arch VARCHAR(32) NULL,
  agent_version VARCHAR(32) NULL,
  shells VARCHAR(255) NULL,
  last_ip VARCHAR(64) NULL,
  last_seen_utc DATETIME NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'active',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_rmm_agents_uid UNIQUE (agent_uid),
  CONSTRAINT uq_rmm_agents_token UNIQUE (auth_token_hash),
  CONSTRAINT fk_rmm_agents_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_agents_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_agents_company ON rmm_agents (company_id, status);
CREATE INDEX IF NOT EXISTS idx_rmm_agents_asset ON rmm_agents (asset_id);
CREATE INDEX IF NOT EXISTS idx_rmm_agents_tray_device ON rmm_agents (tray_device_id);

CREATE TABLE IF NOT EXISTS rmm_script_runs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  script_id INT NULL,
  script_name VARCHAR(255) NOT NULL,
  script_path VARCHAR(512) NOT NULL,
  language VARCHAR(16) NOT NULL,
  script_content LONGTEXT NOT NULL,
  content_sha256 CHAR(64) NOT NULL,
  agent_id INT NULL,
  company_id INT NOT NULL,
  asset_id INT NULL,
  requested_by_user_id INT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'queued',
  inputs_json LONGTEXT NULL,
  payload_encrypted LONGTEXT NULL,
  timeout_seconds INT NOT NULL DEFAULT 600,
  exit_code INT NULL,
  stdout LONGTEXT NULL,
  stderr LONGTEXT NULL,
  custom_values_json LONGTEXT NULL,
  error_message TEXT NULL,
  queued_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  dispatched_at DATETIME NULL,
  started_at DATETIME NULL,
  completed_at DATETIME NULL,
  expires_at DATETIME NULL,
  CONSTRAINT fk_rmm_runs_script FOREIGN KEY (script_id) REFERENCES rmm_scripts(id) ON DELETE SET NULL,
  CONSTRAINT fk_rmm_runs_agent FOREIGN KEY (agent_id) REFERENCES rmm_agents(id) ON DELETE SET NULL,
  CONSTRAINT fk_rmm_runs_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_runs_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_runs_agent_status ON rmm_script_runs (agent_id, status);
CREATE INDEX IF NOT EXISTS idx_rmm_runs_company ON rmm_script_runs (company_id, queued_at);
CREATE INDEX IF NOT EXISTS idx_rmm_runs_asset ON rmm_script_runs (asset_id, queued_at);
CREATE INDEX IF NOT EXISTS idx_rmm_runs_status_expiry ON rmm_script_runs (status, expires_at);
