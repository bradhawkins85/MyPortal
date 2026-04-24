-- Per-company visibility of report sections for the Company Overview report.
-- A row with enabled = 1 means the section is shown; enabled = 0 hides it.
-- Missing rows are treated as enabled (i.e. new sections are visible by default).
CREATE TABLE IF NOT EXISTS company_report_sections (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    section_key VARCHAR(64) NOT NULL,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_company_report_section (company_id, section_key),
    KEY idx_company_report_sections_company (company_id),
    CONSTRAINT fk_company_report_sections_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
