-- Migration 186: add conditional visibility metadata for staff custom fields

ALTER TABLE staff_custom_field_definitions
    ADD COLUMN condition_parent_name VARCHAR(100) NULL AFTER is_active,
    ADD COLUMN condition_operator ENUM('equals', 'not_equals', 'is_checked', 'is_not_checked') NULL AFTER condition_parent_name,
    ADD COLUMN condition_value VARCHAR(255) NULL AFTER condition_operator;
