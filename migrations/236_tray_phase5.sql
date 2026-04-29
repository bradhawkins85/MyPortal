-- Migration 236: Tray App Phase 5 – diagnostics and version tracking
-- Adds the tray_diagnostics table for uploaded log bundles and a
-- tray_version table so admins can publish installer download URLs
-- that the service auto-update checker polls.
--
-- Idempotent: uses CREATE TABLE IF NOT EXISTS / ALTER TABLE IF NOT EXISTS.

-- Uploaded diagnostic log bundles from enrolled tray devices.
CREATE TABLE IF NOT EXISTS tray_diagnostics (
  id INT PRIMARY KEY AUTO_INCREMENT,
  device_id INT NOT NULL,
  filename VARCHAR(255) NOT NULL,
  content_type VARCHAR(128) NOT NULL DEFAULT 'application/zip',
  size_bytes INT NOT NULL DEFAULT 0,
  stored_path VARCHAR(500) NOT NULL,
  uploaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reviewed_by_user_id INT NULL,
  reviewed_at DATETIME NULL,
  notes TEXT NULL,
  CONSTRAINT fk_tray_diagnostics_device FOREIGN KEY (device_id) REFERENCES tray_devices (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tray_diagnostics_device ON tray_diagnostics (device_id);
CREATE INDEX IF NOT EXISTS idx_tray_diagnostics_uploaded ON tray_diagnostics (uploaded_at);

-- Published installer versions for the auto-update endpoint.
-- Only the most recent row with enabled=1 is returned by GET /api/tray/version.
CREATE TABLE IF NOT EXISTS tray_versions (
  id INT PRIMARY KEY AUTO_INCREMENT,
  version VARCHAR(32) NOT NULL,
  platform VARCHAR(16) NOT NULL DEFAULT 'all',
  download_url VARCHAR(500) NOT NULL,
  required TINYINT(1) NOT NULL DEFAULT 0,
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  release_notes TEXT NULL,
  published_by_user_id INT NULL,
  published_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tray_versions_platform ON tray_versions (platform, enabled, published_at);

-- Per-company push-notification toggle (Phase 6).
-- Allows admins to enable OS-native desktop notifications pushed from the
-- server to enrolled tray devices.
ALTER TABLE companies ADD COLUMN IF NOT EXISTS tray_notifications_enabled TINYINT(1) NOT NULL DEFAULT 0;
