-- Migration 202: Offboarding request fields - OOO, email forwarding, mailbox access
-- Adds per-staff offboarding request data columns and a per-company email forwarding toggle.

-- Per-staff columns to capture request-time offboarding preferences.
ALTER TABLE staff
    ADD COLUMN IF NOT EXISTS offboarding_out_of_office TEXT NULL,
    ADD COLUMN IF NOT EXISTS offboarding_email_forward_to VARCHAR(255) NULL,
    ADD COLUMN IF NOT EXISTS offboarding_mailbox_grant_emails TEXT NULL;

-- Per-company toggle: allow companies to disable the email-forwarding field
-- on the offboarding request form (e.g. they prefer a fixed forwarding address).
-- Enabled by default so existing companies retain full behaviour.
ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS offboarding_email_forwarding_enabled TINYINT(1) NOT NULL DEFAULT 1;
