-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 456: Let technicians ignore false user-reported email alerts so
-- the Reported Emails page hides them by default.
CREATE TABLE IF NOT EXISTS m365_reported_email_ignores (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    alert_id VARCHAR(191) NOT NULL,
    ignored_by INT NULL,
    ignored_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_m365_reported_email_ignores_alert UNIQUE (company_id, alert_id),
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    FOREIGN KEY (ignored_by) REFERENCES users(id) ON DELETE SET NULL
);
