ALTER TABLE imap_accounts
    ADD COLUMN priority SMALLINT NOT NULL DEFAULT 100 AFTER id;

UPDATE imap_accounts
SET priority = 100
WHERE priority IS NULL;
