-- Configurable, row-based layout for the Company Overview report.
-- Missing rows use the application default template, so every existing and
-- newly-created company immediately receives a useful report.
CREATE TABLE IF NOT EXISTS company_report_layouts (
    company_id INT NOT NULL PRIMARY KEY,
    layout_json LONGTEXT NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_company_report_layout_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
