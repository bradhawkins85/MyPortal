-- Migration 180: Add per-company M365 admin app configuration
--
-- Moves the M365 admin enterprise app configuration from the global m365-admin
-- integration module to per-company storage in company_m365_credentials.
-- This allows each company to have their own admin app registration for
-- provisioning, configured via /m365 instead of the modules page.
--
-- New columns:
--   admin_client_id          – Admin app Application (client) ID
--   admin_client_secret      – Admin app client secret (encrypted)
--   admin_tenant_id          – Admin app tenant ID (partner tenant)
--   admin_app_object_id      – Admin app object ID (for secret renewal)
--   admin_secret_key_id      – Current secret key ID (for rotation)
--   admin_secret_expires_at  – Admin secret expiry date
--   pkce_client_id           – Auto-provisioned PKCE public client app ID

ALTER TABLE company_m365_credentials
    ADD COLUMN IF NOT EXISTS admin_client_id VARCHAR(255) NULL AFTER client_secret_expires_at,
    ADD COLUMN IF NOT EXISTS admin_client_secret TEXT NULL AFTER admin_client_id,
    ADD COLUMN IF NOT EXISTS admin_tenant_id VARCHAR(255) NULL AFTER admin_client_secret,
    ADD COLUMN IF NOT EXISTS admin_app_object_id VARCHAR(255) NULL AFTER admin_tenant_id,
    ADD COLUMN IF NOT EXISTS admin_secret_key_id VARCHAR(255) NULL AFTER admin_app_object_id,
    ADD COLUMN IF NOT EXISTS admin_secret_expires_at DATETIME NULL AFTER admin_secret_key_id,
    ADD COLUMN IF NOT EXISTS pkce_client_id VARCHAR(255) NULL AFTER admin_secret_expires_at;
