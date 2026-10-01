-- Store privacy-sensitive DMARC forensic (RUF/ARF) report metadata only.
CREATE TABLE IF NOT EXISTS dmarc_forensic_reports (
  id INTEGER PRIMARY KEY AUTO_INCREMENT, company_id INTEGER NOT NULL, import_id INTEGER NOT NULL,
  feedback_type VARCHAR(32) NOT NULL, user_agent VARCHAR(255) NULL, report_version VARCHAR(32) NULL,
  arrival_at DATETIME NULL, source_ip VARCHAR(45) NULL, reported_domain VARCHAR(255) NULL,
  delivery_result VARCHAR(64) NULL, auth_failure VARCHAR(255) NULL, authentication_results TEXT NULL,
  original_mail_from VARCHAR(1000) NULL, original_rcpt_to VARCHAR(1000) NULL,
  dkim_domain VARCHAR(255) NULL, dkim_selector VARCHAR(255) NULL, identity_alignment VARCHAR(64) NULL,
  attachment_sha256 CHAR(64) NOT NULL, content_sha256 CHAR(64) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_dmarc_forensic_report (company_id, attachment_sha256, content_sha256),
  KEY idx_dmarc_forensic_company_date (company_id, arrival_at),
  KEY idx_dmarc_forensic_domain (reported_domain), KEY idx_dmarc_forensic_source_ip (source_ip),
  CONSTRAINT fk_dmarc_forensic_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_dmarc_forensic_import FOREIGN KEY (import_id) REFERENCES dmarc_imports(id) ON DELETE RESTRICT
);
