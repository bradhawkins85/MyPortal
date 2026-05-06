-- Microsoft 365 Enterprise App Permission Diagnostics
--
-- Stores per-company results of enterprise app permission checks.
-- One row per (company, app, role) combination, updated on each check run.
--
-- The DDL below is written to be portable between MySQL and SQLite:
--   * id columns use ``INTEGER PRIMARY KEY AUTO_INCREMENT`` so the
--     SQLite adapter (which rewrites AUTO_INCREMENT → AUTOINCREMENT
--     but does not reorder column attributes) produces valid SQLite.
--   * UNIQUE/INDEX definitions are issued as separate statements
--     because MySQL inline ``UNIQUE KEY``/``INDEX`` syntax is not
--     supported by SQLite.

CREATE TABLE IF NOT EXISTS m365_permission_check_results (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    company_id INT NOT NULL,
    app_id VARCHAR(100) NOT NULL,
    app_name VARCHAR(255) NOT NULL,
    role_id VARCHAR(100) NOT NULL,
    role_name VARCHAR(255) NOT NULL,
    status VARCHAR(10) NOT NULL DEFAULT 'unknown',
    checked_at DATETIME NOT NULL,
    CONSTRAINT chk_m365_perm_status CHECK (status IN ('pass', 'fail', 'unknown'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_m365_perm_check
    ON m365_permission_check_results (company_id, app_id, role_id);

CREATE INDEX IF NOT EXISTS idx_m365_perm_company
    ON m365_permission_check_results (company_id);
