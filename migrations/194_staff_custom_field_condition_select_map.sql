-- Migration 194: allow select_map operator for staff custom field visibility conditions

ALTER TABLE staff_custom_field_definitions
    MODIFY COLUMN condition_operator ENUM('equals', 'not_equals', 'is_checked', 'is_not_checked', 'select_map') NULL;
