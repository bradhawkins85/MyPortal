-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 574: installed printers per RMM device.
--
-- Printers are read by a built-in PowerShell scan (Get-Printer plus each
-- signed-in user's printer connections) that MyPortal queues through the
-- existing RMM script runner, so no new agent version is needed.
-- rmm_printer_scans holds the latest scan per agent: when it ran, when MyPortal
-- last asked for one (so failed scans are retried later rather than on every
-- tick) and what could not be read. rmm_printers lists every printer a scan
-- has found: first_seen_at is when it first appeared, last_seen_at the latest
-- scan that still reported it, and is_installed whether the latest scan did.
-- Printers no longer reported are kept until unseen for over a year.

CREATE TABLE IF NOT EXISTS rmm_printer_scans (
  id INT AUTO_INCREMENT PRIMARY KEY,
  agent_id INT NOT NULL,
  company_id INT NULL,
  asset_id INT NULL,
  run_id INT NULL,
  scanned_at DATETIME NULL,
  received_at DATETIME NULL,
  scan_requested_at DATETIME NULL,
  scan_requested_by_user_id INT NULL,
  printer_count INT NOT NULL DEFAULT 0,
  errors_json TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_rmm_printer_scans_agent UNIQUE (agent_id),
  CONSTRAINT fk_rmm_printer_scans_agent FOREIGN KEY (agent_id) REFERENCES rmm_agents(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_printer_scans_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_printer_scans_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_printer_scans_received ON rmm_printer_scans (received_at);

CREATE TABLE IF NOT EXISTS rmm_printers (
  id INT AUTO_INCREMENT PRIMARY KEY,
  agent_id INT NOT NULL,
  name VARCHAR(255) NOT NULL,
  driver_name VARCHAR(255) NULL,
  port_name VARCHAR(255) NULL,
  port_address VARCHAR(255) NULL,
  printer_type VARCHAR(32) NULL,
  device_type VARCHAR(32) NULL,
  status VARCHAR(64) NULL,
  is_shared TINYINT(1) NOT NULL DEFAULT 0,
  share_name VARCHAR(255) NULL,
  is_published TINYINT(1) NOT NULL DEFAULT 0,
  location VARCHAR(255) NULL,
  comment VARCHAR(255) NULL,
  computer_name VARCHAR(255) NULL,
  connected_users VARCHAR(512) NULL,
  default_for VARCHAR(512) NULL,
  is_installed TINYINT(1) NOT NULL DEFAULT 1,
  first_seen_at DATETIME NOT NULL,
  last_seen_at DATETIME NOT NULL,
  CONSTRAINT fk_rmm_printers_agent FOREIGN KEY (agent_id) REFERENCES rmm_agents(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_printers_agent ON rmm_printers (agent_id);
CREATE INDEX IF NOT EXISTS idx_rmm_printers_last_seen ON rmm_printers (last_seen_at);
