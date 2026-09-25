-- Distinguish Graph operations from mail-transport delivery evidence.
ALTER TABLE ticket_reply_email_recipients
    ADD COLUMN IF NOT EXISTS m365_operation VARCHAR(32) NULL COMMENT 'inbox_item or send_mail',
    ADD COLUMN IF NOT EXISTS m365_state VARCHAR(32) NULL COMMENT 'created, submitted, failed, or unknown',
    ADD COLUMN IF NOT EXISTS m365_read_state VARCHAR(16) NULL COMMENT 'read, unread, or unknown';

UPDATE ticket_reply_email_recipients
   SET m365_operation = 'inbox_item', m365_state = 'created',
       last_event_type = 'created', email_delivered_at = NULL
 WHERE m365_message_id IS NOT NULL AND m365_operation IS NULL;
