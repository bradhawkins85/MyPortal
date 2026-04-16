-- Migration 206: expand condition_value column to TEXT to support long values

ALTER TABLE staff_custom_field_definitions
    MODIFY COLUMN condition_value TEXT NULL;
