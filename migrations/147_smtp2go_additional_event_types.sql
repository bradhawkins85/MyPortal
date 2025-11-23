-- Add additional SMTP2Go event types
-- Support for 'processed' and 'rejected' webhook events

-- Add columns to track processed and rejected timestamps in ticket_replies
ALTER TABLE ticket_replies 
ADD COLUMN IF NOT EXISTS email_processed_at DATETIME(6) NULL COMMENT 'Timestamp when email was accepted by SMTP2Go',
ADD COLUMN IF NOT EXISTS email_rejected_at DATETIME(6) NULL COMMENT 'Timestamp when email was rejected';

-- Update email_tracking_events to support additional event types
ALTER TABLE email_tracking_events
MODIFY COLUMN event_type ENUM('open', 'click', 'delivered', 'bounce', 'spam', 'processed', 'rejected') NOT NULL COMMENT 'Type of tracking event';
