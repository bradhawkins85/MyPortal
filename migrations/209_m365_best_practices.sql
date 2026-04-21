-- Microsoft 365 Best Practices
--
-- Stores per-company results of Best Practice checks and a global
-- enable/disable list maintained by super administrators.
--
-- Best Practices are enabled globally across all companies (the
-- m365_best_practice_settings table holds one row per check_id) and
-- the m365_best_practice_results table records the most recent
-- evaluation result per (company, check).
--
-- The DDL below is written to be portable between MySQL and SQLite:
--   * id columns use ``INTEGER PRIMARY KEY AUTO_INCREMENT`` so the
--     SQLite adapter (which rewrites AUTO_INCREMENT → AUTOINCREMENT
--     but does not reorder column attributes) produces valid SQLite.
--   * UNIQUE/INDEX definitions are issued as separate statements
--     because MySQL inline ``UNIQUE KEY``/``INDEX`` syntax is not
--     supported by SQLite.

CREATE TABLE IF NOT EXISTS m365_best_practice_results (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    company_id INT NOT NULL,
    check_id VARCHAR(100) NOT NULL,
    check_name VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'unknown',
    details TEXT,
    run_at DATETIME NOT NULL,
    CONSTRAINT chk_m365_bp_status CHECK (status IN ('pass', 'fail', 'unknown', 'not_applicable'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_m365_bp_check
    ON m365_best_practice_results (company_id, check_id);

CREATE INDEX IF NOT EXISTS idx_m365_bp_company
    ON m365_best_practice_results (company_id);

CREATE INDEX IF NOT EXISTS idx_m365_bp_run_at
    ON m365_best_practice_results (company_id, run_at);

CREATE TABLE IF NOT EXISTS m365_best_practice_settings (
    check_id VARCHAR(100) NOT NULL PRIMARY KEY,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    updated_at DATETIME NOT NULL
);

