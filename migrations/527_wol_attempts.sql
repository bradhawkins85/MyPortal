-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 527: Track Wake-on-LAN (WoL) attempts so technicians can see
-- whether remotely waking an offline device succeeds, and which on-network
-- agents broadcast the magic packet.
--
-- A single row is created per manual "Wake" action. status moves:
--   pending -> awaiting (packet(s) broadcast by on-network agents)
--   pending/awaiting -> awake | failed | timeout
-- sender_device_ids is a JSON array of tray device ids that sent the packet.

CREATE TABLE IF NOT EXISTS wol_attempts (
  id INT PRIMARY KEY AUTO_INCREMENT,
  company_id INT NOT NULL,
  asset_id INT NULL,
  target_device_id INT NULL,
  target_mac VARCHAR(64) NOT NULL,
  subnet VARCHAR(50) NULL,
  trigger_type VARCHAR(16) NOT NULL DEFAULT 'manual',
  initiated_by_user_id INT NULL,
  sender_device_ids JSON NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  error TEXT NULL,
  woken_at DATETIME NULL,
  woken_via VARCHAR(32) NULL,
  check_deadline DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_wol_attempts_company
      FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_wol_attempts_asset
      FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE SET NULL,
  CONSTRAINT fk_wol_attempts_target_device
      FOREIGN KEY (target_device_id) REFERENCES tray_devices(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_wol_attempts_asset ON wol_attempts (asset_id, created_at);
CREATE INDEX IF NOT EXISTS idx_wol_attempts_status ON wol_attempts (status, check_deadline);
CREATE INDEX IF NOT EXISTS idx_wol_attempts_company ON wol_attempts (company_id);
