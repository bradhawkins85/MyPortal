-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 469: remote control through RustDesk and MeshCentral.
--
-- rmm_remote_control_providers holds one row per provider ('rustdesk' or
-- 'meshcentral'): whether it is on, the RMM script that switches it on for a
-- session, the MeshCentral address, user and login token key (encrypted), and
-- the asset custom field holding each device's ID when no script reports one.
-- entries_encrypted holds the values given to the script's parameters (such as
-- {{company.variables.RustDeskKey}} for installing on devices without the tool).
--
-- rmm_remote_sessions is one technician's request to connect to a device. The
-- activation script runs as an RMM run (run_source 'remote_control') and prints
-- ##myportal[session.id]=... lines; values_encrypted keeps what it returned
-- (it may include a one-time password) until the session expires.

CREATE TABLE IF NOT EXISTS rmm_remote_control_providers (
  provider VARCHAR(16) NOT NULL PRIMARY KEY,
  is_enabled TINYINT(1) NOT NULL DEFAULT 0,
  activation_script_id INT NULL,
  server_url VARCHAR(512) NULL,
  username VARCHAR(255) NULL,
  secret_encrypted TEXT NULL,
  id_field VARCHAR(255) NULL,
  entries_encrypted LONGTEXT NULL,
  updated_by_user_id INT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_rmm_remote_providers_script FOREIGN KEY (activation_script_id) REFERENCES rmm_scripts(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rmm_remote_sessions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  provider VARCHAR(16) NOT NULL,
  company_id INT NOT NULL,
  asset_id INT NOT NULL,
  agent_id INT NULL,
  run_id INT NULL,
  requested_by_user_id INT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'activating',
  values_encrypted TEXT NULL,
  error_message VARCHAR(1024) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ready_at DATETIME NULL,
  expires_at DATETIME NOT NULL,
  CONSTRAINT fk_rmm_remote_sessions_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_remote_sessions_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_remote_sessions_run ON rmm_remote_sessions (run_id);
CREATE INDEX IF NOT EXISTS idx_rmm_remote_sessions_asset ON rmm_remote_sessions (asset_id, created_at);
