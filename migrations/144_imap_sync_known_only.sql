-- Add sync_known_only option to IMAP accounts
-- This allows filtering IMAP sync to only process emails from known email addresses

ALTER TABLE imap_accounts
ADD COLUMN IF NOT EXISTS sync_known_only TINYINT(1) NOT NULL DEFAULT 0
AFTER mark_as_read;
