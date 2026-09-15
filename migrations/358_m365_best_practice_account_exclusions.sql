-- Per-account exclusions for Microsoft 365 best-practice findings.
ALTER TABLE m365_best_practice_results ADD COLUMN affected_accounts TEXT;

CREATE TABLE IF NOT EXISTS m365_best_practice_account_exclusions (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    company_id INT NOT NULL,
    check_id VARCHAR(100) NOT NULL,
    account_id VARCHAR(255) NOT NULL,
    account_name VARCHAR(255) NOT NULL,
    created_at DATETIME NOT NULL,
    CONSTRAINT fk_m365_bp_account_exclusion_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_m365_bp_account_exclusion
    ON m365_best_practice_account_exclusions (company_id, check_id, account_id);
CREATE INDEX IF NOT EXISTS idx_m365_bp_account_exclusion_company
    ON m365_best_practice_account_exclusions (company_id, check_id);
