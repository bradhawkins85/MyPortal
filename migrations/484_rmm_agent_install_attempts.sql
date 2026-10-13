-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 484: history of RMM agent install attempts.
--
-- Every push of the RMM agent (a technician on the Assets page, a company-wide
-- push or an automatic install when a tray device connects) adds a row, also
-- when MyPortal could not send it (no tray app, tray app too old, no agent
-- build): outcome is then 'rejected' and message says why. A push that went
-- to the tray app links its install_rmm_agent tray command (command_id), whose
-- status and error in tray_command_log tell how the install went.
-- source is manual, company or auto.
CREATE TABLE IF NOT EXISTS rmm_agent_install_attempts (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NULL,
  asset_id INT NULL,
  tray_device_id INT NULL,
  command_id INT NULL,
  source VARCHAR(16) NOT NULL DEFAULT 'manual',
  requested_by_user_id INT NULL,
  outcome VARCHAR(16) NOT NULL,
  message TEXT NULL,
  created_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_agent_install_attempts_asset ON rmm_agent_install_attempts (asset_id, id);
CREATE INDEX IF NOT EXISTS idx_rmm_agent_install_attempts_device ON rmm_agent_install_attempts (tray_device_id, id);
