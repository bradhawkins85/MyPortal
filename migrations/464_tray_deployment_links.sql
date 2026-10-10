-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 464: Tray deployment URLs and per-company Windows installer builds.
--
-- A deployment link is a per-company, unguessable URL that serves a
-- self-contained tray installer bundle with the portal address and a company
-- install token already filled in. The slug is stored as an HMAC hash for
-- lookup and encrypted so administrators can copy the link again later. Each
-- link owns one tray install token; revoking the link revokes that token.

CREATE TABLE IF NOT EXISTS tray_deployment_links (
  id INT PRIMARY KEY AUTO_INCREMENT,
  company_id INT NOT NULL,
  label VARCHAR(150) NOT NULL,
  slug_hash VARCHAR(128) NOT NULL,
  slug_prefix VARCHAR(16) NOT NULL,
  slug_encrypted TEXT NOT NULL,
  install_token_id INT NULL,
  install_token_encrypted TEXT NOT NULL,
  created_by_user_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  revoked_at DATETIME NULL,
  download_count INT NOT NULL DEFAULT 0,
  last_downloaded_at DATETIME NULL,
  CONSTRAINT uq_tray_deployment_link_slug_hash UNIQUE (slug_hash)
);

CREATE INDEX IF NOT EXISTS idx_tray_deployment_links_company ON tray_deployment_links (company_id);

-- Company-specific Windows installers (MSI and EXE bundle) are compiled and
-- signed by a Windows build agent, which claims queued rows over the API and
-- uploads the finished files. Only the newest ready build per link is served.
CREATE TABLE IF NOT EXISTS tray_deployment_builds (
  id INT PRIMARY KEY AUTO_INCREMENT,
  deployment_link_id INT NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'queued',
  release_tag VARCHAR(128) NULL,
  error TEXT NULL,
  msi_sha256 VARCHAR(64) NULL,
  exe_sha256 VARCHAR(64) NULL,
  requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  claimed_at DATETIME NULL,
  completed_at DATETIME NULL
);

CREATE INDEX IF NOT EXISTS idx_tray_deployment_builds_link ON tray_deployment_builds (deployment_link_id, status);
