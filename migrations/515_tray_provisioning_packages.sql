-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Optional OOBE provisioning configuration and encrypted package passwords.
-- Configuration contains local administrator and optional Wi-Fi credentials,
-- encrypted with the portal's versioned application encryption key.
ALTER TABLE tray_deployment_links
  ADD COLUMN IF NOT EXISTS provisioning_encrypted TEXT NULL;

ALTER TABLE tray_deployment_builds
  ADD COLUMN IF NOT EXISTS ppkg_sha256 VARCHAR(64) NULL;

ALTER TABLE tray_deployment_builds
  ADD COLUMN IF NOT EXISTS ppkg_password_encrypted TEXT NULL;
