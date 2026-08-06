-- Preserve the configurable Enabled standard field on onboarding requests.
ALTER TABLE staff_requests
    ADD COLUMN IF NOT EXISTS enabled TINYINT(1) NOT NULL DEFAULT 1 AFTER department;
