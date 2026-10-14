-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
CREATE TABLE IF NOT EXISTS rmm_patch_policies (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  company_id INT NULL,
  kind VARCHAR(16) NOT NULL,
  package_id VARCHAR(128) NOT NULL DEFAULT '',
  settings_json LONGTEXT NOT NULL,
  target_rules_json LONGTEXT NULL,
  cron VARCHAR(128) NOT NULL,
  timezone VARCHAR(64) NOT NULL,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  next_run_at DATETIME NULL,
  revision INT NOT NULL DEFAULT 1,
  updated_by_user_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_rmm_patch_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE INDEX IF NOT EXISTS idx_rmm_patch_due ON rmm_patch_policies (next_run_at);
CREATE INDEX IF NOT EXISTS idx_rmm_patch_company ON rmm_patch_policies (company_id);
