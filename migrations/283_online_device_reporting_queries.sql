-- Add current-company scoped online device reports for the Reporting page.
-- Idempotent via INSERT IGNORE on the unique slug.
INSERT IGNORE INTO reporting_queries (slug, name, description, sql_query, is_system)
VALUES
    (
        'online-physical-servers-last-30-days',
        'Online Physical Servers - Last 30 Days',
        'Physical server assets for the current company that have synced in the last 30 days.',
        'SELECT a.id AS asset_id, a.name AS device_name, c.name AS company, COALESCE(NULLIF(TRIM(a.machine_type), ''''), NULLIF(TRIM(a.type), ''''), ''(unspecified)'') AS device_type, a.status, a.os_name, a.serial_number, a.last_sync FROM assets a JOIN companies c ON c.id = a.company_id WHERE a.company_id = {{current.company}} AND a.last_sync IS NOT NULL AND a.last_sync >= (CURRENT_DATE - INTERVAL 30 DAY) AND (LOWER(COALESCE(a.machine_type, '''')) IN (''physical_server'', ''physical server'', ''server_physical'') OR (LOWER(COALESCE(a.type, '''')) LIKE ''%server%'' AND LOWER(COALESCE(a.type, '''')) NOT LIKE ''%virtual%'' AND LOWER(COALESCE(a.type, '''')) NOT LIKE ''%vm%'')) ORDER BY a.last_sync DESC, a.name ASC',
        1
    ),
    (
        'online-virtual-servers-last-30-days',
        'Online Virtual Servers - Last 30 Days',
        'Virtual server assets for the current company that have synced in the last 30 days.',
        'SELECT a.id AS asset_id, a.name AS device_name, c.name AS company, COALESCE(NULLIF(TRIM(a.machine_type), ''''), NULLIF(TRIM(a.type), ''''), ''(unspecified)'') AS device_type, a.status, a.os_name, a.serial_number, a.last_sync FROM assets a JOIN companies c ON c.id = a.company_id WHERE a.company_id = {{current.company}} AND a.last_sync IS NOT NULL AND a.last_sync >= (CURRENT_DATE - INTERVAL 30 DAY) AND (LOWER(COALESCE(a.machine_type, '''')) IN (''virtual_server'', ''virtual server'', ''server_virtual'', ''vm'') OR LOWER(COALESCE(a.type, '''')) LIKE ''%virtual%'' OR LOWER(COALESCE(a.type, '''')) LIKE ''%vm%'') ORDER BY a.last_sync DESC, a.name ASC',
        1
    ),
    (
        'online-workstations-last-30-days',
        'Online Workstations - Last 30 Days',
        'Workstation assets for the current company that have synced in the last 30 days.',
        'SELECT a.id AS asset_id, a.name AS device_name, c.name AS company, COALESCE(NULLIF(TRIM(a.machine_type), ''''), NULLIF(TRIM(a.type), ''''), ''(unspecified)'') AS device_type, a.status, a.os_name, a.serial_number, a.last_sync FROM assets a JOIN companies c ON c.id = a.company_id WHERE a.company_id = {{current.company}} AND a.last_sync IS NOT NULL AND a.last_sync >= (CURRENT_DATE - INTERVAL 30 DAY) AND (LOWER(COALESCE(a.machine_type, '''')) IN (''workstation'', ''desktop'', ''laptop'') OR LOWER(COALESCE(a.type, '''')) LIKE ''%workstation%'' OR LOWER(COALESCE(a.type, '''')) LIKE ''%desktop%'' OR LOWER(COALESCE(a.type, '''')) LIKE ''%laptop%'') ORDER BY a.last_sync DESC, a.name ASC',
        1
    ),
    (
        'online-device-custom-field-last-30-days',
        'Online Device - Custom Field - Last 30 Days',
        'Online assets for the current company in the last 30 days with their configured custom field values.',
        'SELECT a.id AS asset_id, a.name AS device_name, c.name AS company, COALESCE(NULLIF(TRIM(a.machine_type), ''''), NULLIF(TRIM(a.type), ''''), ''(unspecified)'') AS device_type, a.status, a.last_sync, acfd.name AS custom_field, CASE WHEN acfd.field_type = ''checkbox'' THEN CASE WHEN acfv.value_boolean = 1 THEN ''true'' WHEN acfv.value_boolean = 0 THEN ''false'' ELSE NULL END WHEN acfd.field_type = ''date'' THEN CAST(acfv.value_date AS CHAR) ELSE acfv.value_text END AS custom_field_value FROM assets a JOIN companies c ON c.id = a.company_id LEFT JOIN asset_custom_field_values acfv ON acfv.asset_id = a.id LEFT JOIN asset_custom_field_definitions acfd ON acfd.id = acfv.field_definition_id WHERE a.company_id = {{current.company}} AND a.last_sync IS NOT NULL AND a.last_sync >= (CURRENT_DATE - INTERVAL 30 DAY) ORDER BY a.last_sync DESC, a.name ASC, acfd.display_order ASC, acfd.name ASC',
        1
    );
