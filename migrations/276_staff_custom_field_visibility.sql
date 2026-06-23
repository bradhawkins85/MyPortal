-- Migration 276: add requester visibility limits for staff custom fields

ALTER TABLE staff_custom_field_definitions
    ADD COLUMN IF NOT EXISTS visible_to_job_titles TEXT NULL AFTER condition_value;

ALTER TABLE staff_custom_field_definitions
    ADD COLUMN IF NOT EXISTS visible_to_requester_emails TEXT NULL AFTER visible_to_job_titles;
