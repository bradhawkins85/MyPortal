-- Extend m365_permission_check_results.status to allow 'not_supported'.
--
-- 'not_supported' is used when a required permission's role GUID does not
-- exist on the resource service principal in the tenant (e.g.
-- SharePointTenantSettings.Read.All on a tenant whose Microsoft Graph SP
-- has not yet been updated to expose that role).  This distinguishes a
-- permission that cannot be granted in this tenant from one that simply
-- hasn't been granted yet ('fail').
--
-- Because SQLite does not support ALTER TABLE ... DROP/ADD CONSTRAINT, we
-- recreate the table using the portable 12-step procedure.  The migration
-- runner silently continues past statements that are not supported on a
-- given engine.

-- Step 1: Create replacement table with the updated CHECK constraint.
-- AUTO_INCREMENT is required for MySQL so that INSERT statements that omit
-- the id column receive an auto-assigned value.  The migration runner
-- transforms AUTO_INCREMENT → AUTOINCREMENT for SQLite automatically.
CREATE TABLE IF NOT EXISTS m365_permission_check_results_v2 (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    company_id INT NOT NULL,
    app_id VARCHAR(100) NOT NULL,
    app_name VARCHAR(255) NOT NULL,
    role_id VARCHAR(100) NOT NULL,
    role_name VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'unknown',
    checked_at DATETIME NOT NULL,
    CONSTRAINT chk_m365_perm_status CHECK (status IN ('pass', 'fail', 'unknown', 'not_supported'))
);

-- Step 2: Copy existing data.
INSERT INTO m365_permission_check_results_v2
    (id, company_id, app_id, app_name, role_id, role_name, status, checked_at)
SELECT id, company_id, app_id, app_name, role_id, role_name, status, checked_at
FROM m365_permission_check_results;

-- Step 3: Drop old table.
DROP TABLE m365_permission_check_results;

-- Step 4: Rename new table.
ALTER TABLE m365_permission_check_results_v2 RENAME TO m365_permission_check_results;

-- Step 5: Recreate indexes.
CREATE UNIQUE INDEX IF NOT EXISTS uq_m365_perm_check
    ON m365_permission_check_results (company_id, app_id, role_id);

CREATE INDEX IF NOT EXISTS idx_m365_perm_company
    ON m365_permission_check_results (company_id);
