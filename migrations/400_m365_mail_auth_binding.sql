-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Keep mailbox authentication identity independent from ticket routing.

ALTER TABLE m365_mail_accounts
  ADD COLUMN IF NOT EXISTS auth_company_id INT NULL,
  ADD COLUMN IF NOT EXISTS auth_connection_id INT NULL,
  ADD COLUMN IF NOT EXISTS auth_tenant_id VARCHAR(255) NULL,
  ADD COLUMN IF NOT EXISTS auth_binding_status VARCHAR(32) NOT NULL DEFAULT 'unbound',
  ADD COLUMN IF NOT EXISTS app_fallback_enabled TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS mailbox_app_authorized TINYINT(1) NOT NULL DEFAULT 0;

-- Preserve established company credential users only when their existing
-- company has one unambiguous active, tenant-verified connection.
UPDATE m365_mail_accounts a
JOIN m365_connections c ON c.company_id = a.company_id
  AND c.state = 'active' AND c.verification_tenant = 1
  AND c.verified_at IS NOT NULL
SET a.auth_company_id = c.company_id,
    a.auth_connection_id = c.id,
    a.auth_tenant_id = c.tenant_id,
    a.auth_binding_status = 'verified'
WHERE a.refresh_token IS NULL
  AND (a.auth_binding_status = 'unbound' OR a.auth_binding_status IS NULL)
  AND NOT EXISTS (
    SELECT 1 FROM m365_connections c2
    WHERE c2.company_id = a.company_id AND c2.state = 'active' AND c2.id <> c.id
  );

-- Delegated rows already contain an issuer tenant. Preserve their grants and
-- flag them as verified bindings without changing cursors, messages or jobs.
UPDATE m365_mail_accounts
SET auth_tenant_id = tenant_id, auth_binding_status = 'verified'
WHERE refresh_token IS NOT NULL AND tenant_id IS NOT NULL
  AND (auth_binding_status = 'unbound' OR auth_binding_status IS NULL);

UPDATE m365_mail_accounts
SET auth_binding_status = 'repair_required'
WHERE auth_binding_status = 'unbound'
  AND (company_id IS NOT NULL OR refresh_token IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_m365_mail_accounts_auth_connection
  ON m365_mail_accounts (auth_connection_id, auth_binding_status);
