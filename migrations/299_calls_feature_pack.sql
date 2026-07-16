-- Calls feature pack: central ActionURL-compatible phone call event log.
CREATE TABLE IF NOT EXISTS phone_call_events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    webhook_token VARCHAR(255) NOT NULL,
    event_name VARCHAR(64) NULL,
    remote_number VARCHAR(128) NULL,
    local_number VARCHAR(128) NULL,
    call_id VARCHAR(255) NULL,
    direction VARCHAR(64) NULL,
    duration_seconds INT NULL,
    supported_params JSON NOT NULL,
    raw_params JSON NOT NULL,
    source_ip VARCHAR(64) NULL,
    user_agent VARCHAR(512) NULL,
    received_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    INDEX idx_phone_call_events_received_at (received_at),
    INDEX idx_phone_call_events_call_id (call_id),
    INDEX idx_phone_call_events_remote_number (remote_number),
    INDEX idx_phone_call_events_webhook_token (webhook_token)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET @calls_webhook_token = LOWER(REPLACE(UUID(), '-', ''));

INSERT INTO integration_modules (slug, name, description, icon, enabled, settings)
VALUES (
    'calls',
    'Calls',
    'Receive ActionURL-compatible phone events over HTTP GET and log supported call metadata in a central list.',
    '📞',
    1,
    JSON_OBJECT(
        'webhook_token', @calls_webhook_token,
        'webhook_path', CONCAT('/phonewebhook/', @calls_webhook_token, '/'),
        'supported_variables', JSON_ARRAY('phone_ip', 'mac', 'product', 'program_version', 'hardware_version', 'language', 'local', 'display_local', 'remote', 'display_remote', 'call-id', 'active_user', 'active_host', 'duration', 'calldirection')
    )
) ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description),
    icon = VALUES(icon),
    settings = CASE
        WHEN JSON_UNQUOTE(JSON_EXTRACT(settings, '$.webhook_token')) IS NULL
          OR JSON_UNQUOTE(JSON_EXTRACT(settings, '$.webhook_token')) = ''
          OR JSON_UNQUOTE(JSON_EXTRACT(settings, '$.webhook_token')) = 'obscure_static_id_for_security'
        THEN VALUES(settings)
        ELSE JSON_SET(
            settings,
            '$.webhook_path',
            CONCAT('/phonewebhook/', JSON_UNQUOTE(JSON_EXTRACT(settings, '$.webhook_token')), '/')
        )
    END;
