ALTER TABLE ticket_replies
    ADD COLUMN minutes_spent INT NULL;

ALTER TABLE ticket_replies
    ADD COLUMN is_billable TINYINT(1) NOT NULL DEFAULT 0;
