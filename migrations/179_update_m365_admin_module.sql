-- Migration 179: Update m365-admin module description
-- CSP partner mode has been replaced with per-tenant enterprise apps.

UPDATE integration_modules 
SET name = 'Microsoft 365 Admin',
    description = 'Configure Microsoft 365 admin app credentials for enterprise app management.'
WHERE slug = 'm365-admin';
