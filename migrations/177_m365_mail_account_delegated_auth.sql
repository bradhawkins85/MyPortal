-- Add per-account delegated-user OAuth tokens to m365_mail_accounts so that
-- each mailbox import can authenticate independently of the CSP / company
-- credentials.  The admin signs in as a user with access to the shared
-- mailbox and the resulting refresh token is stored here.

ALTER TABLE m365_mail_accounts
  ADD COLUMN tenant_id VARCHAR(255) NULL AFTER company_id,
  ADD COLUMN refresh_token TEXT NULL AFTER tenant_id,
  ADD COLUMN access_token TEXT NULL AFTER refresh_token,
  ADD COLUMN token_expires_at DATETIME NULL AFTER access_token;
