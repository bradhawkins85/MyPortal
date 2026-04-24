-- Per-company report-level settings for the Company Overview report.
-- auto_hide_empty: when 1 (default), sections with no content are hidden.
-- section_order: JSON array of section keys in the desired display order.
--   NULL means use the canonical default order.
CREATE TABLE IF NOT EXISTS company_report_settings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    auto_hide_empty TINYINT(1) NOT NULL DEFAULT 1,
    section_order TEXT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_company_report_settings (company_id),
    CONSTRAINT fk_company_report_settings_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
