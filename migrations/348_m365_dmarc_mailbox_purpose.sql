-- Route a selected Microsoft 365 mailbox to DMARC ingestion instead of tickets.
ALTER TABLE m365_mail_accounts
  ADD COLUMN IF NOT EXISTS import_purpose VARCHAR(32) NOT NULL DEFAULT 'support_ticket';

CREATE INDEX IF NOT EXISTS idx_m365_mail_accounts_import_purpose
  ON m365_mail_accounts (import_purpose, active);
