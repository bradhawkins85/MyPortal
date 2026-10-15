-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Local copy of the stable Chocolatey community catalog, refreshed by the
-- update_chocolatey_catalog scheduled task so searches and upgrade checks do
-- not query the public feed on every request.
CREATE TABLE IF NOT EXISTS rmm_chocolatey_packages (
  package_id VARCHAR(128) NOT NULL PRIMARY KEY,
  title VARCHAR(255) NOT NULL,
  version VARCHAR(64) NOT NULL,
  download_count BIGINT NOT NULL DEFAULT 0,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_chocolatey_packages_downloads ON rmm_chocolatey_packages (download_count);
