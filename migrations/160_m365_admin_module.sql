-- Migration 160: Add Microsoft 365 CSP / Lighthouse integration module
-- This module stores the M365 admin (partner) client_id and client_secret
-- so that admins can configure CSP / Lighthouse credentials via the UI
-- instead of requiring environment variables.

INSERT INTO integration_modules (slug, name, description, icon, enabled, settings)
VALUES (
    'm365-admin',
    'Microsoft 365 CSP / Lighthouse',
    'Configure Microsoft 365 CSP / Lighthouse partner credentials to enumerate and manage customer tenants.',
    '☁️',
    0,
    JSON_OBJECT('client_id', '', 'client_secret', '')
)
ON DUPLICATE KEY UPDATE
    name        = VALUES(name),
    description = VALUES(description),
    icon        = VALUES(icon);
