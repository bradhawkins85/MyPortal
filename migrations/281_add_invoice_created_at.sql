ALTER TABLE invoices
    ADD COLUMN created_at DATETIME NULL;

UPDATE invoices
SET created_at = CURRENT_TIMESTAMP
WHERE created_at IS NULL;
