-- Per-recipient email delivery tracking for ticket replies.
-- Adds a row per To/CC/BCC recipient on each send, so the single delivery
-- status badge on a reply can be expanded into a popup showing exactly
-- which recipient has received, opened, bounced, etc.
--
-- The existing aggregate columns on ticket_replies are kept unchanged so
-- the single-status badge keeps working; this table is purely additive.

CREATE TABLE IF NOT EXISTS ticket_reply_email_recipients (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ticket_reply_id INT NOT NULL COMMENT 'References ticket_replies.id',
    recipient_email VARCHAR(320) NOT NULL COMMENT 'Normalised lowercase recipient email',
    recipient_role ENUM('to', 'cc', 'bcc') NOT NULL DEFAULT 'to' COMMENT 'Recipient role on the message',
    recipient_name VARCHAR(255) NULL COMMENT 'Display name when available',
    tracking_id VARCHAR(64) NULL COMMENT 'Internal email_tracking_id for this send',
    smtp2go_message_id VARCHAR(128) NULL COMMENT 'SMTP2Go message ID returned by API',
    email_sent_at DATETIME(6) NULL COMMENT 'When this recipient was sent (or processed)',
    email_processed_at DATETIME(6) NULL COMMENT 'SMTP2Go processed event timestamp',
    email_delivered_at DATETIME(6) NULL COMMENT 'Delivered event timestamp',
    email_opened_at DATETIME(6) NULL COMMENT 'First open timestamp',
    email_open_count INT NOT NULL DEFAULT 0 COMMENT 'Number of opens recorded',
    email_bounced_at DATETIME(6) NULL COMMENT 'Bounce event timestamp',
    email_rejected_at DATETIME(6) NULL COMMENT 'Rejected event timestamp',
    email_spam_at DATETIME(6) NULL COMMENT 'Spam complaint timestamp',
    last_event_at DATETIME(6) NULL COMMENT 'Timestamp of the most recent event',
    last_event_type VARCHAR(32) NULL COMMENT 'Most recent event type seen',
    last_event_detail TEXT NULL COMMENT 'Optional detail (e.g. bounce reason)',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    INDEX idx_reply_recipients_reply (ticket_reply_id),
    INDEX idx_reply_recipients_tracking (tracking_id),
    INDEX idx_reply_recipients_smtp2go (smtp2go_message_id),
    INDEX idx_reply_recipients_recipient (recipient_email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
