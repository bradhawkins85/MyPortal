-- Migration 207: add multiselect to staff_custom_field_definitions field_type enum

ALTER TABLE staff_custom_field_definitions
    MODIFY COLUMN field_type ENUM('text', 'checkbox', 'date', 'select', 'multiselect') NOT NULL DEFAULT 'text';
