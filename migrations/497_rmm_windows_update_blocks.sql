-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 497: blocked Windows updates.
--
-- rmm_windows_update_blocks holds the updates technicians blocked: for one
-- device (asset_id, with its company_id), one company (company_id only) or
-- every company (both NULL). A block names a KB number, which covers every
-- revision and architecture of that update, or the update ID of an update
-- without one. MyPortal never installs a blocked update, and the device hides
-- it from Windows Update at its next scan.
-- rmm_windows_update_hidden records which updates MyPortal hid on each
-- device, so unblocking shows them again without touching updates someone
-- else hid. rmm_windows_updates.is_hidden is whether the update is hidden on
-- the device.

CREATE TABLE IF NOT EXISTS rmm_windows_update_blocks (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NULL,
  asset_id INT NULL,
  kb VARCHAR(16) NULL,
  update_id VARCHAR(64) NULL,
  title VARCHAR(512) NOT NULL,
  reason VARCHAR(500) NULL,
  created_by_user_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_rmm_windows_update_blocks_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_windows_update_blocks_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_windows_update_blocks_company ON rmm_windows_update_blocks (company_id);
CREATE INDEX IF NOT EXISTS idx_rmm_windows_update_blocks_asset ON rmm_windows_update_blocks (asset_id);

CREATE TABLE IF NOT EXISTS rmm_windows_update_hidden (
  id INT AUTO_INCREMENT PRIMARY KEY,
  agent_id INT NOT NULL,
  update_id VARCHAR(64) NOT NULL,
  hidden_at DATETIME NOT NULL,
  CONSTRAINT uq_rmm_windows_update_hidden UNIQUE (agent_id, update_id),
  CONSTRAINT fk_rmm_windows_update_hidden_agent FOREIGN KEY (agent_id) REFERENCES rmm_agents(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE rmm_windows_updates ADD COLUMN IF NOT EXISTS is_hidden TINYINT(1) NOT NULL DEFAULT 0;
