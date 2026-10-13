-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 483: multi-line text asset custom fields.
--
-- Adds the 'textarea' field type so asset custom fields can hold multi-line
-- text. Values continue to be stored in asset_custom_field_values.value_text.

ALTER TABLE asset_custom_field_definitions
    MODIFY field_type ENUM('text', 'textarea', 'image', 'checkbox', 'url', 'date') NOT NULL DEFAULT 'text';
