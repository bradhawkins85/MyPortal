-- Microsoft 365 Best Practices
--
-- Stores per-company results of Best Practice checks and a global
-- enable/disable list maintained by super administrators.
--
-- Best Practices are enabled globally across all companies (the
-- m365_best_practice_settings table holds one row per check_id) and
-- the m365_best_practice_results table records the most recent
-- evaluation result per (company, check).

CREATE TABLE IF NOT EXISTS m365_best_practice_results (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    check_id VARCHAR(100) NOT NULL,
    check_name VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'unknown',
    details TEXT,
    run_at DATETIME NOT NULL,
    UNIQUE KEY uq_m365_bp_check (company_id, check_id),
    INDEX idx_m365_bp_company (company_id),
    INDEX idx_m365_bp_run_at (company_id, run_at),
    CONSTRAINT chk_m365_bp_status CHECK (status IN ('pass', 'fail', 'unknown', 'not_applicable'))
);

CREATE TABLE IF NOT EXISTS m365_best_practice_settings (
    check_id VARCHAR(100) NOT NULL PRIMARY KEY,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
