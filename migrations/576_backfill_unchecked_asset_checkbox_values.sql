-- phase: backfill
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- A checkbox custom field is always ticked or unticked. Store an explicit
-- unchecked (0) value for assets that never had one, so the Assets table and
-- RMM check targeting see "unchecked" instead of "not set".
INSERT INTO asset_custom_field_values (asset_id, field_definition_id, value_boolean)
SELECT a.id, d.id, 0
FROM assets a
JOIN asset_custom_field_definitions d ON d.field_type = 'checkbox'
WHERE NOT EXISTS (
    SELECT 1 FROM asset_custom_field_values v
    WHERE v.asset_id = a.id AND v.field_definition_id = d.id
);

UPDATE asset_custom_field_values
SET value_boolean = 0
WHERE value_boolean IS NULL
  AND field_definition_id IN (
      SELECT d.id FROM asset_custom_field_definitions d WHERE d.field_type = 'checkbox'
  );
