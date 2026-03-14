-- Migration 162: Extend m365-admin module settings for auto-provisioning
--
-- Adds new JSON fields to the m365-admin integration module settings so that
-- auto-provisioned CSP/Lighthouse admin app registration details can be stored
-- alongside the existing client_id and client_secret:
--
--   tenant_id               – partner tenant ID (for client_credentials renewal)
--   app_object_id           – Azure AD app registration object ID (for addPassword)
--   client_secret_key_id    – current secret key ID (for removePassword on renewal)
--   client_secret_expires_at – UTC expiry so scheduler can trigger renewal
--
-- This migration is idempotent: it only updates the row if it exists and
-- does not overwrite existing values for the new fields.

UPDATE integration_modules
SET settings = JSON_MERGE_PATCH(
    COALESCE(settings, '{}'),
    JSON_OBJECT(
        'tenant_id',               COALESCE(JSON_UNQUOTE(JSON_EXTRACT(settings, '$.tenant_id')), ''),
        'app_object_id',           COALESCE(JSON_UNQUOTE(JSON_EXTRACT(settings, '$.app_object_id')), ''),
        'client_secret_key_id',    COALESCE(JSON_UNQUOTE(JSON_EXTRACT(settings, '$.client_secret_key_id')), ''),
        'client_secret_expires_at',COALESCE(JSON_UNQUOTE(JSON_EXTRACT(settings, '$.client_secret_expires_at')), '')
    )
)
WHERE slug = 'm365-admin';
