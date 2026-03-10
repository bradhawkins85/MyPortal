-- Add Apprise integration module for sending notifications to 80+ services
INSERT INTO integration_modules (slug, name, description, icon, enabled, settings)
VALUES (
    'apprise',
    'Apprise',
    'Send notifications to 80+ services via Apprise notification URLs.',
    '🔔',
    0,
    JSON_OBJECT(
        'urls', JSON_ARRAY(),
        'title', ''
    )
) ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description),
    icon = VALUES(icon);
