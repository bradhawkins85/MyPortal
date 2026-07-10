-- Store an inbound reply author snapshot for email-only watchers and SMS senders
ALTER TABLE ticket_replies
ADD COLUMN IF NOT EXISTS author_email VARCHAR(255) NULL AFTER author_id;

ALTER TABLE ticket_replies
ADD COLUMN IF NOT EXISTS author_display_name VARCHAR(255) NULL AFTER author_email;

CREATE INDEX IF NOT EXISTS idx_ticket_replies_author_email
    ON ticket_replies (author_email);
