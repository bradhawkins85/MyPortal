-- Allow inbound email replies with long quoted histories or inline content to be stored.
-- MySQL TEXT is limited to 64 KiB and caused M365 reply appends to fail with
-- "Data too long for column 'body'". SQLite treats LONGTEXT as TEXT affinity.
ALTER TABLE ticket_replies
  MODIFY COLUMN body LONGTEXT NOT NULL;
