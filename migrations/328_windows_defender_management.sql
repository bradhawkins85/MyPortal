-- Windows Defender management is opt-in per company. The tray agent is the
-- transport for status, policy and detection data.
ALTER TABLE companies
  ADD COLUMN IF NOT EXISTS defender_enabled TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS defender_scheduled_scan_type VARCHAR(16) NULL;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS defender_scheduled_scan_day TINYINT NULL;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS defender_scheduled_scan_time TIME NULL;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS defender_auto_ticket_min_severity VARCHAR(16) NULL;

CREATE TABLE IF NOT EXISTS defender_device_status (
  tray_device_id INT PRIMARY KEY,
  company_id INT NOT NULL,
  enabled TINYINT(1) NOT NULL DEFAULT 0,
  antivirus_enabled TINYINT(1) NOT NULL DEFAULT 0,
  realtime_protection_enabled TINYINT(1) NOT NULL DEFAULT 0,
  tamper_protection_enabled TINYINT(1) NOT NULL DEFAULT 0,
  signatures_updated_at DATETIME NULL,
  last_scan_at DATETIME NULL,
  threat_count INT NOT NULL DEFAULT 0,
  health_status VARCHAR(32) NOT NULL DEFAULT 'unknown',
  details_json JSON NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_defender_status_device FOREIGN KEY (tray_device_id) REFERENCES tray_devices(id) ON DELETE CASCADE,
  CONSTRAINT fk_defender_status_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS defender_exclusions (
  id INT PRIMARY KEY AUTO_INCREMENT,
  scope VARCHAR(16) NOT NULL,
  company_id INT NULL,
  tray_device_id INT NULL,
  exclusion_type VARCHAR(16) NOT NULL,
  value VARCHAR(1000) NOT NULL,
  created_by_user_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_defender_exclusion_company (company_id),
  CONSTRAINT fk_defender_exclusion_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_defender_exclusion_device FOREIGN KEY (tray_device_id) REFERENCES tray_devices(id) ON DELETE CASCADE,
  CONSTRAINT fk_defender_exclusion_user FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS defender_detections (
  id INT PRIMARY KEY AUTO_INCREMENT,
  company_id INT NOT NULL,
  tray_device_id INT NOT NULL,
  detection_uid VARCHAR(255) NOT NULL,
  threat_name VARCHAR(500) NOT NULL,
  severity VARCHAR(32) NOT NULL DEFAULT 'unknown',
  status VARCHAR(32) NOT NULL DEFAULT 'active',
  detected_at DATETIME NOT NULL,
  details_json JSON NULL,
  ticket_id INT NULL,
  acknowledged_at DATETIME NULL,
  acknowledged_by_user_id INT NULL,
  resolved_at DATETIME NULL,
  resolved_by_user_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_defender_detection_device_uid (tray_device_id, detection_uid),
  CONSTRAINT fk_defender_detection_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_defender_detection_device FOREIGN KEY (tray_device_id) REFERENCES tray_devices(id) ON DELETE CASCADE,
  CONSTRAINT fk_defender_detection_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE SET NULL,
  CONSTRAINT fk_defender_detection_ack_user FOREIGN KEY (acknowledged_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
  CONSTRAINT fk_defender_detection_resolve_user FOREIGN KEY (resolved_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS defender_commands (
  id INT PRIMARY KEY AUTO_INCREMENT,
  company_id INT NOT NULL,
  tray_device_id INT NOT NULL,
  detection_id INT NULL,
  command_type VARCHAR(32) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  requested_by_user_id INT NULL,
  requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  claimed_at DATETIME NULL,
  completed_at DATETIME NULL,
  result_json JSON NULL,
  KEY idx_defender_commands_poll (tray_device_id, status, requested_at),
  CONSTRAINT fk_defender_command_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_defender_command_device FOREIGN KEY (tray_device_id) REFERENCES tray_devices(id) ON DELETE CASCADE,
  CONSTRAINT fk_defender_command_detection FOREIGN KEY (detection_id) REFERENCES defender_detections(id) ON DELETE SET NULL,
  CONSTRAINT fk_defender_command_user FOREIGN KEY (requested_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);
