-- Correlation data for messages deposited directly into Microsoft 365 inboxes.
ALTER TABLE ticket_reply_email_recipients
    ADD COLUMN IF NOT EXISTS m365_message_id VARCHAR(512) NULL COMMENT 'Graph message ID for a directly deposited message',
    ADD COLUMN IF NOT EXISTS m365_company_id INT NULL COMMENT 'Company whose M365 credentials own the mailbox',
    ADD INDEX IF NOT EXISTS idx_reply_recipients_m365_message (m365_message_id(191));

INSERT INTO integration_modules (slug, name, description, icon, enabled, settings)
VALUES (
    'm365-direct-delivery',
    'M365 Direct Delivery',
    'Deposit notifications directly into Microsoft 365 inboxes without SMTP transport.',
    '📨',
    0,
    JSON_OBJECT('company_id', 0, 'recipient_domains', JSON_ARRAY(), 'fallback_to_smtp', true, 'track_read_status', true)
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name), description = VALUES(description), icon = VALUES(icon);
