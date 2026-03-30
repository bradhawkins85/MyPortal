-- Migration 193: add optional grouping for staff custom fields

ALTER TABLE staff_custom_field_definitions
    ADD COLUMN IF NOT EXISTS field_group VARCHAR(120) NULL AFTER field_type;
