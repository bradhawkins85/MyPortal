-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 519: AI health checks and the logs the AI agents need.
--
-- ai_health_checks keeps one row per health check run on an asset's tray
-- device (app.services.ai_health_check). The device sends a snapshot of the
-- computer's health (snapshot_json: built-in Windows metrics, disk health,
-- chkdsk and defrag state, thermals and, on physical hardware, sensors read
-- through PawnIO while it is briefly installed) and the logs it was asked
-- for (logs_json keeps the redacted, sampled text the model read). The
-- language model's report (report_json: findings and the steps
-- that bring the computer back to optimal performance) is stored with it.
-- ticket_id is set when the check was started from a ticket.
--
-- ai_required_logs lists log sources the AI troubleshooter or health check
-- asked for but could not read: reason_code 'not_allowed' when the source is
-- not on the approved list, 'unavailable' when the endpoint could not read it
-- (channel disabled or missing, file not found, access denied). One row per
-- platform, reason and source (source_key is a hash of the three). A super
-- admin approves a 'not_allowed' row on /admin/required-logs, which adds
-- approve_value (an event log channel or a log folder) to the approved list
-- the agents use, or dismisses it.

CREATE TABLE IF NOT EXISTS ai_health_checks (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  asset_id INT NOT NULL,
  tray_device_id INT NULL,
  ticket_id INT NULL,
  command_id INT NULL,
  status VARCHAR(24) NOT NULL DEFAULT 'queued',
  stage_message VARCHAR(1024) NULL,
  platform VARCHAR(16) NULL,
  model VARCHAR(255) NULL,
  allow_pawnio TINYINT(1) NOT NULL DEFAULT 0,
  pawnio_status VARCHAR(512) NULL,
  overall_status VARCHAR(16) NULL,
  health_score INT NULL,
  summary TEXT NULL,
  snapshot_json LONGTEXT NULL,
  report_json LONGTEXT NULL,
  logs_json LONGTEXT NULL,
  log_requests_json TEXT NULL,
  error VARCHAR(1024) NULL,
  requested_by_user_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at DATETIME NULL,
  CONSTRAINT fk_ai_health_checks_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_ai_health_checks_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_ai_health_checks_asset ON ai_health_checks (asset_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_health_checks_created ON ai_health_checks (created_at);
CREATE INDEX IF NOT EXISTS idx_ai_health_checks_command ON ai_health_checks (command_id);

CREATE TABLE IF NOT EXISTS ai_required_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  source_key CHAR(64) NOT NULL,
  platform VARCHAR(16) NOT NULL,
  kind VARCHAR(16) NOT NULL,
  source VARCHAR(512) NOT NULL,
  reason_code VARCHAR(16) NOT NULL,
  reason TEXT NULL,
  detail VARCHAR(1024) NULL,
  features VARCHAR(128) NOT NULL DEFAULT '',
  approve_kind VARCHAR(16) NULL,
  approve_value VARCHAR(512) NULL,
  occurrences INT NOT NULL DEFAULT 1,
  status VARCHAR(16) NOT NULL DEFAULT 'open',
  status_note VARCHAR(500) NULL,
  status_changed_by_user_id INT NULL,
  status_changed_at DATETIME NULL,
  last_company_id INT NULL,
  last_asset_id INT NULL,
  last_ticket_id INT NULL,
  last_health_check_id INT NULL,
  first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_required_logs_source_key ON ai_required_logs (source_key);
CREATE INDEX IF NOT EXISTS idx_ai_required_logs_status ON ai_required_logs (status, last_seen_at);
