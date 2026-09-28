-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 440: SMB1001 evidence files, recommended product/service help
-- links per control, and reporting queries for company reports. These mirror
-- the Essential 8 equivalents.

CREATE TABLE IF NOT EXISTS company_smb1001_evidence (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  control_id INT NOT NULL,
  version_number INT NOT NULL DEFAULT 1,
  title VARCHAR(255) NOT NULL,
  description TEXT NULL,
  file_name VARCHAR(255) NOT NULL,
  content_type VARCHAR(255) NULL,
  file_path VARCHAR(500) NOT NULL,
  file_size_bytes BIGINT NULL,
  uploaded_by INT NULL,
  uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  is_current TINYINT(1) NOT NULL DEFAULT 1,
  CONSTRAINT fk_smb1001_evidence_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_smb1001_evidence_control FOREIGN KEY (control_id) REFERENCES smb1001_controls(id) ON DELETE CASCADE,
  CONSTRAINT fk_smb1001_evidence_user FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE SET NULL,
  CONSTRAINT uq_smb1001_evidence_version UNIQUE (company_id, control_id, version_number)
);

CREATE INDEX idx_smb1001_evidence_lookup ON company_smb1001_evidence (company_id, control_id, is_current);

CREATE TABLE IF NOT EXISTS smb1001_control_marketing_pages (
  control_id INT NOT NULL PRIMARY KEY,
  marketing_page_id INT NULL,
  recommendation_name VARCHAR(255) NULL,
  external_url VARCHAR(2048) NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_smb1001_help_control FOREIGN KEY (control_id) REFERENCES smb1001_controls(id) ON DELETE CASCADE,
  CONSTRAINT fk_smb1001_help_page FOREIGN KEY (marketing_page_id) REFERENCES marketing_pages(id) ON DELETE SET NULL
);

INSERT INTO reporting_queries (slug, name, description, sql_query, is_system) VALUES
('report-smb1001-compliance-progress', 'Report - SMB1001 Compliance Progress', 'SMB1001 controls by tier with the current company''s status.', 'SELECT t.name AS tier, c.code, c.name AS control_name, COALESCE(cc.status, ''not_started'') AS status, cc.notes, cc.updated_at FROM smb1001_controls c JOIN smb1001_tiers t ON t.tier_level = c.tier_level LEFT JOIN company_smb1001_compliance cc ON cc.control_id = c.id AND cc.company_id = {{current.company}} ORDER BY c.tier_level, c.code', 1),
('stat-strip-report-smb1001', 'Stat Strip - Company Overview - SMB1001', 'SMB1001 complete, in-progress, non-compliant and not-started control counts by tier.', 'SELECT t.name AS tier, SUM(CASE WHEN cc.status IN (''compliant'', ''not_applicable'') THEN 1 ELSE 0 END) AS complete, SUM(CASE WHEN cc.status = ''in_progress'' THEN 1 ELSE 0 END) AS in_progress, SUM(CASE WHEN cc.status = ''non_compliant'' THEN 1 ELSE 0 END) AS non_compliant, SUM(CASE WHEN cc.status IS NULL OR cc.status = ''not_started'' THEN 1 ELSE 0 END) AS not_started FROM smb1001_controls c JOIN smb1001_tiers t ON t.tier_level = c.tier_level LEFT JOIN company_smb1001_compliance cc ON cc.control_id = c.id AND cc.company_id = {{current.company}} GROUP BY t.tier_level, t.name ORDER BY t.tier_level', 1);
