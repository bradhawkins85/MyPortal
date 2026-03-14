-- Migration 161: Track M365 app registration details for automatic secret renewal
--
-- app_object_id          – Azure AD application object ID (used to call addPassword)
-- client_secret_key_id   – Key ID of the current client secret (used to revoke old key)
-- client_secret_expires_at – UTC datetime when the current client secret expires

ALTER TABLE company_m365_credentials
    ADD COLUMN IF NOT EXISTS app_object_id VARCHAR(255) NULL AFTER client_secret,
    ADD COLUMN IF NOT EXISTS client_secret_key_id VARCHAR(255) NULL AFTER app_object_id,
    ADD COLUMN IF NOT EXISTS client_secret_expires_at DATETIME NULL AFTER client_secret_key_id;
