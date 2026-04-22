-- Microsoft 365 Best Practices – per-company check exclusions
--
-- Allows individual companies to opt out of specific best-practice checks
-- without affecting the global enabled/auto-remediate settings that apply
-- to all other companies.
--
-- When a row exists for (company_id, check_id) the check is skipped during
-- evaluation and hidden from the company's best-practices page, even if the
-- check is globally enabled.

CREATE TABLE IF NOT EXISTS m365_best_practice_company_exclusions (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    company_id INT NOT NULL,
    check_id VARCHAR(100) NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_m365_bp_company_excl
    ON m365_best_practice_company_exclusions (company_id, check_id);

CREATE INDEX IF NOT EXISTS idx_m365_bp_excl_company
    ON m365_best_practice_company_exclusions (company_id);
