-- Email tracking tables for Plausible Analytics integration
-- Tracks email opens and link clicks for ticket replies sent via email

-- Add tracking columns to ticket_replies table
ALTER TABLE ticket_replies 
ADD COLUMN IF NOT EXISTS email_tracking_id VARCHAR(64) NULL COMMENT 'Unique tracking ID for email open tracking',
ADD COLUMN IF NOT EXISTS email_sent_at DATETIME(6) NULL COMMENT 'Timestamp when email was sent',
ADD COLUMN IF NOT EXISTS email_opened_at DATETIME(6) NULL COMMENT 'Timestamp of first email open',
ADD COLUMN IF NOT EXISTS email_open_count INT NOT NULL DEFAULT 0 COMMENT 'Number of times email was opened',
ADD INDEX idx_ticket_replies_tracking_id (email_tracking_id);

-- Create email tracking events table for detailed tracking
CREATE TABLE IF NOT EXISTS email_tracking_events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    tracking_id VARCHAR(64) NOT NULL COMMENT 'References ticket_replies.email_tracking_id',
    event_type ENUM('open', 'click') NOT NULL COMMENT 'Type of tracking event',
    event_url VARCHAR(2048) NULL COMMENT 'URL clicked (for click events)',
    user_agent TEXT NULL COMMENT 'User agent from tracking request',
    ip_address VARCHAR(45) NULL COMMENT 'IP address from tracking request',
    referrer VARCHAR(2048) NULL COMMENT 'Referrer from tracking request',
    occurred_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'When the event occurred',
    plausible_sent TINYINT(1) NOT NULL DEFAULT 0 COMMENT 'Whether event was sent to Plausible',
    plausible_sent_at DATETIME(6) NULL COMMENT 'When event was sent to Plausible',
    INDEX idx_email_tracking_events_tracking_id (tracking_id),
    INDEX idx_email_tracking_events_type (event_type),
    INDEX idx_email_tracking_events_occurred (occurred_at),
    INDEX idx_email_tracking_events_plausible_sent (plausible_sent)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Insert Plausible Analytics integration module
INSERT INTO integration_modules (slug, name, description, icon, enabled, settings)
VALUES (
    'plausible',
    'Plausible Analytics',
    'Privacy-friendly analytics for email tracking. Track email opens and link clicks without compromising user privacy.',
    '📊',
    0,
    JSON_OBJECT(
        'base_url', '',
        'site_domain', '',
        'api_key', '',
        'track_opens', true,
        'track_clicks', true,
        'send_to_plausible', false
    )
)
ON DUPLICATE KEY UPDATE
    description = VALUES(description),
    icon = VALUES(icon);
