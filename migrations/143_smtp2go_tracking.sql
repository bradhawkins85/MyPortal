-- SMTP2Go email tracking enhancements
-- Adds columns for SMTP2Go message IDs and delivery tracking

-- Add SMTP2Go tracking columns to ticket_replies table
ALTER TABLE ticket_replies 
ADD COLUMN IF NOT EXISTS smtp2go_message_id VARCHAR(128) NULL COMMENT 'SMTP2Go message ID from API response',
ADD COLUMN IF NOT EXISTS email_delivered_at DATETIME(6) NULL COMMENT 'Timestamp when email was delivered',
ADD COLUMN IF NOT EXISTS email_bounced_at DATETIME(6) NULL COMMENT 'Timestamp when email bounced',
ADD INDEX IF NOT EXISTS idx_ticket_replies_smtp2go_message_id (smtp2go_message_id);

-- Add SMTP2Go data column to email_tracking_events for webhook data
ALTER TABLE email_tracking_events
ADD COLUMN IF NOT EXISTS smtp2go_data TEXT NULL COMMENT 'Full SMTP2Go webhook data (JSON)';

-- Update email_tracking_events to support new event types
ALTER TABLE email_tracking_events
MODIFY COLUMN event_type ENUM('open', 'click', 'delivered', 'bounce', 'spam') NOT NULL COMMENT 'Type of tracking event';

-- Insert SMTP2Go integration module
INSERT INTO integration_modules (slug, name, description, icon, enabled, settings)
VALUES (
    'smtp2go',
    'SMTP2Go',
    'Enhanced email delivery via SMTP2Go API with delivery, open, and click tracking.',
    '📧',
    0,
    JSON_OBJECT(
        'api_key', '',
        'enable_tracking', true,
        'track_opens', true,
        'track_clicks', true,
        'webhook_secret', ''
    )
)
ON DUPLICATE KEY UPDATE
    description = VALUES(description),
    icon = VALUES(icon);
