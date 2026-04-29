-- MyPortal Tray App
-- Tables to support the cross-platform desktop tray application that
-- enrolls per-device, fetches a server-driven menu configuration, and
-- exchanges chat sessions with the helpdesk through Matrix.

CREATE TABLE IF NOT EXISTS tray_install_tokens (
  id INT PRIMARY KEY AUTO_INCREMENT,
  company_id INT NULL,
  label VARCHAR(150) NOT NULL,
  token_hash VARCHAR(128) NOT NULL,
  token_prefix VARCHAR(16) NOT NULL,
  created_by_user_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at DATETIME NULL,
  revoked_at DATETIME NULL,
  last_used_at DATETIME NULL,
  use_count INT NOT NULL DEFAULT 0,
  CONSTRAINT uq_tray_install_token_hash UNIQUE (token_hash)
);

CREATE TABLE IF NOT EXISTS tray_devices (
  id INT PRIMARY KEY AUTO_INCREMENT,
  company_id INT NULL,
  asset_id INT NULL,
  device_uid VARCHAR(64) NOT NULL,
  enrolment_token_id INT NULL,
  auth_token_hash VARCHAR(128) NOT NULL,
  auth_token_prefix VARCHAR(16) NOT NULL,
  os VARCHAR(32) NULL,
  os_version VARCHAR(64) NULL,
  hostname VARCHAR(255) NULL,
  serial_number VARCHAR(128) NULL,
  agent_version VARCHAR(32) NULL,
  console_user VARCHAR(255) NULL,
  last_ip VARCHAR(64) NULL,
  last_seen_utc DATETIME NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_tray_device_uid UNIQUE (device_uid)
);

CREATE TABLE IF NOT EXISTS tray_menu_configs (
  id INT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(150) NOT NULL,
  scope VARCHAR(16) NOT NULL DEFAULT 'global',
  scope_ref_id INT NULL,
  payload_json LONGTEXT NOT NULL,
  display_text LONGTEXT NULL,
  env_allowlist TEXT NULL,
  branding_icon_url VARCHAR(500) NULL,
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  version INT NOT NULL DEFAULT 1,
  created_by_user_id INT NULL,
  updated_by_user_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tray_command_log (
  id INT PRIMARY KEY AUTO_INCREMENT,
  device_id INT NOT NULL,
  command VARCHAR(64) NOT NULL,
  payload_json LONGTEXT NULL,
  initiated_by_user_id INT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'queued',
  error TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  delivered_at DATETIME NULL
);

-- Link a chat room back to the originating tray device, when the room
-- was opened via the tray-app push-to-device flow.
ALTER TABLE chat_rooms ADD COLUMN IF NOT EXISTS tray_device_id INT NULL;

-- Per-company toggle for technician-initiated tray chats. Default is OFF
-- so the feature is opt-in per the security model.
ALTER TABLE companies ADD COLUMN IF NOT EXISTS tray_chat_enabled TINYINT(1) NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_tray_devices_company ON tray_devices (company_id);
CREATE INDEX IF NOT EXISTS idx_tray_devices_asset ON tray_devices (asset_id);
CREATE INDEX IF NOT EXISTS idx_tray_devices_status ON tray_devices (status);
CREATE INDEX IF NOT EXISTS idx_tray_devices_last_seen ON tray_devices (last_seen_utc);
CREATE INDEX IF NOT EXISTS idx_tray_install_tokens_company ON tray_install_tokens (company_id);
CREATE INDEX IF NOT EXISTS idx_tray_menu_configs_scope ON tray_menu_configs (scope, scope_ref_id);
CREATE INDEX IF NOT EXISTS idx_tray_command_log_device ON tray_command_log (device_id);
CREATE INDEX IF NOT EXISTS idx_chat_rooms_tray_device ON chat_rooms (tray_device_id);
