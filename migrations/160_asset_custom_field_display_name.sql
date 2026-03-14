-- Add display_name column to asset_custom_field_definitions
-- This allows admins to set a shorter display name for the table column header
-- while keeping the full name for matching software/data.
ALTER TABLE asset_custom_field_definitions
  ADD COLUMN IF NOT EXISTS display_name VARCHAR(255) NULL DEFAULT NULL AFTER name;
