-- Migration 277: add M365 mailbox/group UPN mappings for staff custom fields

ALTER TABLE staff_custom_field_definitions
    ADD COLUMN m365_upn VARCHAR(255) NULL AFTER visible_to_requester_emails;

ALTER TABLE staff_custom_field_options
    ADD COLUMN m365_upn VARCHAR(255) NULL AFTER option_label;
