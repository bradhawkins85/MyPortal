-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Bind OAuth material to the immutable identity that originally issued it.

ALTER TABLE m365_mail_accounts
  ADD COLUMN IF NOT EXISTS oauth_client_id VARCHAR(255) NULL,
  ADD COLUMN IF NOT EXISTS oauth_authority VARCHAR(255) NULL,
  ADD COLUMN IF NOT EXISTS oauth_account_id VARCHAR(255) NULL,
  ADD COLUMN IF NOT EXISTS oauth_scopes TEXT NULL,
  ADD COLUMN IF NOT EXISTS oauth_connection_version INT NULL,
  ADD COLUMN IF NOT EXISTS token_revision INT NOT NULL DEFAULT 0;

ALTER TABLE user_m365_contact_integrations
  ADD COLUMN IF NOT EXISTS oauth_client_id VARCHAR(255) NULL,
  ADD COLUMN IF NOT EXISTS oauth_authority VARCHAR(255) NULL,
  ADD COLUMN IF NOT EXISTS oauth_account_id VARCHAR(255) NULL,
  ADD COLUMN IF NOT EXISTS oauth_scopes TEXT NULL,
  ADD COLUMN IF NOT EXISTS oauth_connection_version INT NULL,
  ADD COLUMN IF NOT EXISTS token_revision INT NOT NULL DEFAULT 0;

ALTER TABLE company_m365_credentials
  ADD COLUMN IF NOT EXISTS token_cache_tenant_id VARCHAR(255) NULL,
  ADD COLUMN IF NOT EXISTS token_cache_client_id VARCHAR(255) NULL,
  ADD COLUMN IF NOT EXISTS token_cache_grant_type VARCHAR(32) NULL,
  ADD COLUMN IF NOT EXISTS app_access_token TEXT NULL,
  ADD COLUMN IF NOT EXISTS app_token_expires_at DATETIME NULL,
  ADD COLUMN IF NOT EXISTS app_token_cache_tenant_id VARCHAR(255) NULL,
  ADD COLUMN IF NOT EXISTS app_token_cache_client_id VARCHAR(255) NULL;
