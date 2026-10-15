-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 498: Windows Update status per RMM device.
--
-- rmm_windows_update_scans holds the latest Windows Update scan each RMM agent
-- reported (one row per agent): when it ran, the update source, a pending
-- restart and what could not be read. missing_checked_at is the last scan whose
-- search for missing updates succeeded. scan_requested_at is when MyPortal last
-- queued a scan, so failed scans are retried later rather than on every tick.
-- rmm_windows_updates lists the updates the device still needs; first_seen_at
-- is kept across scans to show how long each has been missing.
-- rmm_windows_update_history lists the device's own Windows Update history
-- (source 'history') and the hotfixes Windows reports as installed (source
-- 'hotfix'), replaced each time a scan arrives.

CREATE TABLE IF NOT EXISTS rmm_windows_update_scans (
  id INT AUTO_INCREMENT PRIMARY KEY,
  agent_id INT NOT NULL,
  company_id INT NULL,
  asset_id INT NULL,
  run_id INT NULL,
  scanned_at DATETIME NULL,
  received_at DATETIME NULL,
  missing_checked_at DATETIME NULL,
  scan_requested_at DATETIME NULL,
  scan_requested_by_user_id INT NULL,
  update_source VARCHAR(255) NULL,
  reboot_pending TINYINT(1) NOT NULL DEFAULT 0,
  missing_count INT NOT NULL DEFAULT 0,
  errors_json TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_rmm_windows_update_scans_agent UNIQUE (agent_id),
  CONSTRAINT fk_rmm_windows_update_scans_agent FOREIGN KEY (agent_id) REFERENCES rmm_agents(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_windows_update_scans_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_windows_update_scans_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_windows_update_scans_received ON rmm_windows_update_scans (received_at);

CREATE TABLE IF NOT EXISTS rmm_windows_updates (
  id INT AUTO_INCREMENT PRIMARY KEY,
  agent_id INT NOT NULL,
  update_id VARCHAR(64) NOT NULL,
  revision INT NULL,
  title VARCHAR(512) NOT NULL,
  kb VARCHAR(255) NULL,
  classification VARCHAR(64) NULL,
  severity VARCHAR(32) NULL,
  size_bytes BIGINT NULL,
  released_on DATE NULL,
  is_downloaded TINYINT(1) NOT NULL DEFAULT 0,
  reboot_behavior INT NULL,
  is_interactive TINYINT(1) NOT NULL DEFAULT 0,
  is_optional TINYINT(1) NOT NULL DEFAULT 0,
  first_seen_at DATETIME NOT NULL,
  last_seen_at DATETIME NOT NULL,
  CONSTRAINT fk_rmm_windows_updates_agent FOREIGN KEY (agent_id) REFERENCES rmm_agents(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_windows_updates_agent ON rmm_windows_updates (agent_id);
CREATE INDEX IF NOT EXISTS idx_rmm_windows_updates_kb ON rmm_windows_updates (kb);

CREATE TABLE IF NOT EXISTS rmm_windows_update_history (
  id INT AUTO_INCREMENT PRIMARY KEY,
  agent_id INT NOT NULL,
  source VARCHAR(16) NOT NULL,
  update_id VARCHAR(64) NULL,
  title VARCHAR(512) NOT NULL,
  kb VARCHAR(255) NULL,
  classification VARCHAR(64) NULL,
  operation VARCHAR(16) NOT NULL,
  result VARCHAR(32) NOT NULL,
  hresult VARCHAR(10) NULL,
  occurred_at DATETIME NULL,
  client_application VARCHAR(255) NULL,
  CONSTRAINT fk_rmm_windows_update_history_agent FOREIGN KEY (agent_id) REFERENCES rmm_agents(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_windows_update_history_agent ON rmm_windows_update_history (agent_id, occurred_at);
