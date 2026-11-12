-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 558: Windows blue screen dumps reported by tray devices.
--
-- asset_minidumps keeps one row per kernel minidump the tray app found in
-- %SystemRoot%\Minidump on an asset's computer (app.services.minidump_analysis).
-- The dump file never leaves the computer: summary_json is what the tray app
-- decoded from it (stop code and parameters, crash time, the drivers on the
-- crash stack and the loaded driver list). A technician can have the AI
-- troubleshooter explain the crash; its report is kept in analysis_json and
-- ticket_id is the ticket the last analysis was also posted to, if any.
-- A dump is identified by its device, file name, size and modified time, so
-- a repeated report changes nothing.

CREATE TABLE IF NOT EXISTS asset_minidumps (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  asset_id INT NOT NULL,
  tray_device_id INT NOT NULL,
  file_name VARCHAR(255) NOT NULL,
  file_size BIGINT NOT NULL DEFAULT 0,
  file_modified_at DATETIME NOT NULL,
  crashed_at DATETIME NULL,
  bugcheck_code BIGINT NULL,
  bugcheck_name VARCHAR(96) NULL,
  suspect_driver VARCHAR(64) NULL,
  supported TINYINT(1) NOT NULL DEFAULT 1,
  summary_json MEDIUMTEXT NULL,
  analysis_status VARCHAR(16) NOT NULL DEFAULT 'none',
  analysis_json MEDIUMTEXT NULL,
  analysis_error VARCHAR(512) NULL,
  analysis_started_at DATETIME NULL,
  model VARCHAR(255) NULL,
  ticket_id INT NULL,
  analysed_by_user_id INT NULL,
  analysed_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_asset_minidumps_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_asset_minidumps_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_minidumps_file
  ON asset_minidumps (tray_device_id, file_name, file_size, file_modified_at);
CREATE INDEX IF NOT EXISTS idx_asset_minidumps_asset ON asset_minidumps (asset_id, crashed_at);
