-- Add CSP tenant ID mapping to companies
ALTER TABLE companies ADD COLUMN IF NOT EXISTS csp_tenant_id VARCHAR(64) DEFAULT NULL;

-- Store CSP OAuth sessions per admin user (access/refresh tokens encrypted)
CREATE TABLE IF NOT EXISTS admin_csp_sessions (
    user_id      INT NOT NULL,
    access_token TEXT NOT NULL,
    refresh_token TEXT,
    expires_at   DATETIME,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id)
);
