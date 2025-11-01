CREATE TABLE IF NOT EXISTS notification_event_settings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    event_type VARCHAR(150) NOT NULL,
    display_name VARCHAR(150) NOT NULL,
    description TEXT NULL,
    message_template TEXT NOT NULL,
    is_user_visible TINYINT(1) NOT NULL DEFAULT 1,
    allow_channel_in_app TINYINT(1) NOT NULL DEFAULT 1,
    allow_channel_email TINYINT(1) NOT NULL DEFAULT 0,
    allow_channel_sms TINYINT(1) NOT NULL DEFAULT 0,
    default_channel_in_app TINYINT(1) NOT NULL DEFAULT 1,
    default_channel_email TINYINT(1) NOT NULL DEFAULT 0,
    default_channel_sms TINYINT(1) NOT NULL DEFAULT 0,
    module_actions JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_notification_event_settings_event_type (event_type)
);
