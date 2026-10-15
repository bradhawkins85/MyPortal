-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
CREATE TABLE IF NOT EXISTS rmm_chocolatey_sources (
  company_id INT PRIMARY KEY,
  enabled_json LONGTEXT NOT NULL,
  CONSTRAINT fk_choco_source_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
