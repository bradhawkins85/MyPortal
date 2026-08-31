-- Assets retained in MyPortal after removal from Tactical RMM must be visible to
-- billing and reporting workflows.
INSERT INTO asset_custom_field_definitions (name, display_name, field_type, display_order)
SELECT 'Archive Asset', 'Archive Asset', 'checkbox',
       COALESCE((SELECT MAX(existing.display_order) + 1 FROM asset_custom_field_definitions existing), 0)
WHERE NOT EXISTS (
  SELECT 1
  FROM asset_custom_field_definitions
  WHERE LOWER(name) = LOWER('Archive Asset')
);
