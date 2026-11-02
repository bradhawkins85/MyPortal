-- phase: data
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 537: make sure every instance has the "Archive Asset" checkbox
-- custom field (also accepted as ARCHIVE_ASSET), and leave assets with it ticked
-- out of the online physical and virtual server reports.
INSERT INTO asset_custom_field_definitions (name, display_name, field_type, display_order)
SELECT 'Archive Asset', 'Archive Asset', 'checkbox',
       COALESCE((SELECT MAX(existing.display_order) + 1 FROM asset_custom_field_definitions existing), 0)
WHERE NOT EXISTS (
  SELECT 1
  FROM asset_custom_field_definitions
  WHERE LOWER(name) IN ('archive asset', 'archive_asset')
);

UPDATE reporting_queries
SET sql_query = 'SELECT a.id AS asset_id, a.name AS device_name, c.name AS company, COALESCE(NULLIF(TRIM(a.machine_type), ''''), NULLIF(TRIM(a.type), ''''), ''(unspecified)'') AS device_type, a.status, a.os_name, a.serial_number, a.last_sync FROM assets a JOIN companies c ON c.id = a.company_id WHERE a.company_id = {{current.company}} AND a.last_sync IS NOT NULL AND a.last_sync >= (CURRENT_DATE - INTERVAL 30 DAY) AND LOWER(COALESCE(NULLIF(TRIM(a.machine_type), ''''), NULLIF(TRIM(a.type), ''''))) LIKE ''%server%'' AND LOWER(COALESCE(NULLIF(TRIM(a.machine_type), ''''), NULLIF(TRIM(a.type), ''''))) NOT LIKE ''%virtual%'' AND LOWER(COALESCE(NULLIF(TRIM(a.machine_type), ''''), NULLIF(TRIM(a.type), ''''))) NOT LIKE ''%vm%'' AND NOT EXISTS (SELECT 1 FROM asset_custom_field_values v JOIN asset_custom_field_definitions d ON d.id = v.field_definition_id WHERE v.asset_id = a.id AND LOWER(d.name) IN (''archive asset'', ''archive_asset'') AND v.value_boolean = 1) ORDER BY a.last_sync DESC, a.name ASC'
WHERE slug = 'online-physical-servers-last-30-days';

UPDATE reporting_queries
SET sql_query = 'SELECT a.id AS asset_id, a.name AS device_name, c.name AS company, COALESCE(NULLIF(TRIM(a.machine_type), ''''), NULLIF(TRIM(a.type), ''''), ''(unspecified)'') AS device_type, a.status, a.os_name, a.serial_number, a.last_sync FROM assets a JOIN companies c ON c.id = a.company_id WHERE a.company_id = {{current.company}} AND a.last_sync IS NOT NULL AND a.last_sync >= (CURRENT_DATE - INTERVAL 30 DAY) AND (LOWER(COALESCE(NULLIF(TRIM(a.machine_type), ''''), NULLIF(TRIM(a.type), ''''))) LIKE ''%virtual%'' OR LOWER(COALESCE(NULLIF(TRIM(a.machine_type), ''''), NULLIF(TRIM(a.type), ''''))) LIKE ''%vm%'') AND NOT EXISTS (SELECT 1 FROM asset_custom_field_values v JOIN asset_custom_field_definitions d ON d.id = v.field_definition_id WHERE v.asset_id = a.id AND LOWER(d.name) IN (''archive asset'', ''archive_asset'') AND v.value_boolean = 1) ORDER BY a.last_sync DESC, a.name ASC'
WHERE slug = 'online-virtual-servers-last-30-days';
