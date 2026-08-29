ALTER TABLE m365_mail_accounts
  ADD COLUMN IF NOT EXISTS delete_after_import TINYINT(1) NOT NULL DEFAULT 0
  AFTER mark_as_read;
