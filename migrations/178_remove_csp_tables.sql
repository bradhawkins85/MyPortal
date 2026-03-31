-- Migration 178: Remove CSP/Lighthouse related tables and columns
-- CSP partner mode has been replaced with per-tenant enterprise apps.

-- Drop the CSP sessions table (used for storing CSP partner OAuth sessions)
DROP TABLE IF EXISTS admin_csp_sessions;

-- Note: The csp_tenant_id column in companies table is intentionally left in place
-- to avoid data loss. It can be removed in a future migration after confirming
-- no deployments rely on it. The column is no longer read by the application.
