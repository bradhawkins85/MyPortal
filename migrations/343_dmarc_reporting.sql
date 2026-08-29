-- DMARC aggregate reporting. Additive and safe to run repeatedly.
ALTER TABLE companies ADD COLUMN IF NOT EXISTS dmarc_reporting_code VARCHAR(32) NULL;
UPDATE companies
SET dmarc_reporting_code = REPLACE(REPLACE(REPLACE(TO_BASE64(RANDOM_BYTES(18)), '+', '-'), '/', '_'), '=', '')
WHERE dmarc_reporting_code IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_dmarc_reporting_code ON companies (dmarc_reporting_code);

CREATE TABLE IF NOT EXISTS dmarc_imports (
  id INTEGER PRIMARY KEY AUTO_INCREMENT, company_id INTEGER NULL, mailbox_message_id VARCHAR(512) NOT NULL,
  attachment_sha256 CHAR(64) NOT NULL, content_sha256 CHAR(64) NULL, source_filename VARCHAR(255) NOT NULL,
  received_at DATETIME NOT NULL, status VARCHAR(32) NOT NULL DEFAULT 'pending', failure_reason TEXT NULL,
  raw_metadata JSON NULL, attempts INTEGER NOT NULL DEFAULT 0, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_dmarc_delivery (mailbox_message_id, attachment_sha256),
  KEY idx_dmarc_import_company (company_id), KEY idx_dmarc_import_status (status),
  CONSTRAINT fk_dmarc_import_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS dmarc_reports (
  id INTEGER PRIMARY KEY AUTO_INCREMENT, company_id INTEGER NOT NULL, import_id INTEGER NOT NULL,
  reporter VARCHAR(255) NOT NULL, report_id VARCHAR(255) NOT NULL, date_begin DATETIME NOT NULL,
  date_end DATETIME NOT NULL, domain VARCHAR(255) NOT NULL, adkim VARCHAR(16) NULL, aspf VARCHAR(16) NULL,
  policy VARCHAR(16) NOT NULL, subdomain_policy VARCHAR(16) NULL, percentage INTEGER NULL,
  attachment_sha256 CHAR(64) NOT NULL, content_sha256 CHAR(64) NOT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_dmarc_report (company_id, reporter, report_id, attachment_sha256, content_sha256),
  KEY idx_dmarc_report_company_date (company_id, date_begin), KEY idx_dmarc_report_domain (domain),
  CONSTRAINT fk_dmarc_report_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_dmarc_report_import FOREIGN KEY (import_id) REFERENCES dmarc_imports(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS dmarc_records (
  id INTEGER PRIMARY KEY AUTO_INCREMENT, company_id INTEGER NOT NULL, report_id INTEGER NOT NULL,
  source_ip VARCHAR(45) NOT NULL, message_count INTEGER NOT NULL, disposition VARCHAR(16) NOT NULL,
  dkim_result VARCHAR(32) NOT NULL, spf_result VARCHAR(32) NOT NULL, header_from VARCHAR(255) NOT NULL,
  envelope_from VARCHAR(255) NULL, envelope_to VARCHAR(255) NULL,
  KEY idx_dmarc_record_company (company_id), KEY idx_dmarc_record_source_ip (source_ip),
  KEY idx_dmarc_record_domain (header_from), KEY idx_dmarc_record_disposition (disposition),
  CONSTRAINT fk_dmarc_record_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_dmarc_record_report FOREIGN KEY (report_id) REFERENCES dmarc_reports(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS dmarc_auth_results (
  id INTEGER PRIMARY KEY AUTO_INCREMENT, company_id INTEGER NOT NULL, record_id INTEGER NOT NULL,
  mechanism VARCHAR(8) NOT NULL, domain VARCHAR(255) NOT NULL, selector VARCHAR(255) NULL,
  scope VARCHAR(32) NULL, result VARCHAR(32) NOT NULL, human_result TEXT NULL,
  KEY idx_dmarc_auth_company (company_id), KEY idx_dmarc_auth_domain (domain),
  CONSTRAINT fk_dmarc_auth_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_dmarc_auth_record FOREIGN KEY (record_id) REFERENCES dmarc_records(id) ON DELETE CASCADE
);

UPDATE roles SET permissions = JSON_ARRAY_APPEND(COALESCE(permissions, JSON_ARRAY()), '$', 'dmarc.view')
WHERE name IN ('Owner', 'Administrator') AND NOT JSON_CONTAINS(COALESCE(permissions, JSON_ARRAY()), '"dmarc.view"');
UPDATE roles SET permissions = JSON_ARRAY_APPEND(COALESCE(permissions, JSON_ARRAY()), '$', 'dmarc.manage')
WHERE name IN ('Owner', 'Administrator') AND NOT JSON_CONTAINS(COALESCE(permissions, JSON_ARRAY()), '"dmarc.manage"');
