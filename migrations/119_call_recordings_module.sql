-- Add Call Recordings integration module for configuring recordings storage path
INSERT INTO integration_modules (slug, name, description, icon, enabled, settings)
VALUES (
    'call-recordings',
    'Call Recordings',
    'Configure the storage location for call recording files. Set the base directory path where recording files should be stored or retrieved.',
    '📞',
    1,
    JSON_OBJECT(
        'recordings_path', '/var/lib/myportal/call_recordings'
    )
) ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description),
    icon = VALUES(icon);
