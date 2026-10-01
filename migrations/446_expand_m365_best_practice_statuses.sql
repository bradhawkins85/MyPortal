-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Allow the precise assessment outcomes emitted by the M365 best-practice
-- service. Rebuilding the table keeps this migration portable across MySQL
-- and the SQLite fallback, neither of which can portably replace a CHECK.
CREATE TABLE IF NOT EXISTS m365_best_practice_results_v2 (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    company_id INT NOT NULL,
    check_id VARCHAR(100) NOT NULL,
    check_name VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'unknown',
    details TEXT,
    run_at DATETIME NOT NULL,
    remediation_status VARCHAR(20) NULL,
    remediated_at DATETIME NULL,
    affected_accounts TEXT,
    notes TEXT NULL,
    remediation_failure_reason TEXT NULL,
    CONSTRAINT chk_m365_bp_status CHECK (status IN (
        'pass', 'fail', 'unknown', 'not_applicable', 'not_licensed',
        'unsupported', 'permission_missing', 'assessment_failed'
    ))
);

INSERT INTO m365_best_practice_results_v2
    (id, company_id, check_id, check_name, status, details, run_at,
     remediation_status, remediated_at, affected_accounts, notes,
     remediation_failure_reason)
SELECT id, company_id, check_id, check_name, status, details, run_at,
       remediation_status, remediated_at, affected_accounts, notes,
       remediation_failure_reason
FROM m365_best_practice_results;

DROP TABLE m365_best_practice_results;
ALTER TABLE m365_best_practice_results_v2 RENAME TO m365_best_practice_results;

CREATE UNIQUE INDEX IF NOT EXISTS uq_m365_bp_check
    ON m365_best_practice_results (company_id, check_id);
CREATE INDEX IF NOT EXISTS idx_m365_bp_company
    ON m365_best_practice_results (company_id);
CREATE INDEX IF NOT EXISTS idx_m365_bp_run_at
    ON m365_best_practice_results (company_id, run_at);
