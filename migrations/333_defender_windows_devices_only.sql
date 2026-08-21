-- Defender endpoint protection only applies to Windows tray agents. Keep the
-- system reporting catalogue aligned with the Defender management page by
-- excluding active macOS/Linux agents from all device-based results.
UPDATE reporting_queries
SET sql_query = 'SELECT td.hostname AS device, a.name AS asset, COALESCE(ds.health_status, ''not_reporting'') AS health_status, COALESCE(ds.antivirus_enabled, 0) AS antivirus_enabled, COALESCE(ds.realtime_protection_enabled, 0) AS realtime_protection_enabled, COALESCE(ds.tamper_protection_enabled, 0) AS tamper_protection_enabled, ds.signatures_updated_at, ds.last_scan_at, COALESCE(ds.threat_count, 0) AS active_threats, ds.updated_at AS status_updated_at, td.last_seen_utc AS device_last_seen FROM tray_devices td LEFT JOIN assets a ON a.id = td.asset_id LEFT JOIN defender_device_status ds ON ds.tray_device_id = td.id WHERE td.company_id = {{current.company}} AND td.status = ''active'' AND LOWER(td.os) = ''windows'' ORDER BY device ASC'
WHERE slug = 'defender-device-status' AND is_system = 1;

UPDATE reporting_queries
SET sql_query = 'SELECT td.id, td.hostname FROM tray_devices td INNER JOIN defender_device_status ds ON ds.tray_device_id = td.id WHERE td.company_id = {{current.company}} AND td.status = ''active'' AND LOWER(td.os) = ''windows'''
WHERE slug = 'dashboard-defender-devices' AND is_system = 1;

UPDATE reporting_queries
SET sql_query = 'SELECT td.id, td.hostname, COALESCE(ds.health_status, ''not_reporting'') AS health_status FROM tray_devices td LEFT JOIN defender_device_status ds ON ds.tray_device_id = td.id WHERE td.company_id = {{current.company}} AND td.status = ''active'' AND LOWER(td.os) = ''windows'' AND (ds.tray_device_id IS NULL OR LOWER(COALESCE(ds.health_status, ''unknown'')) <> ''healthy'' OR ds.antivirus_enabled = 0 OR ds.realtime_protection_enabled = 0)'
WHERE slug = 'dashboard-defender-unhealthy-devices' AND is_system = 1;

UPDATE reporting_queries
SET sql_query = 'SELECT COALESCE(NULLIF(ds.health_status, ''''), ''not_reporting'') AS X, COUNT(*) AS Y FROM tray_devices td LEFT JOIN defender_device_status ds ON ds.tray_device_id = td.id WHERE td.company_id = {{current.company}} AND td.status = ''active'' AND LOWER(td.os) = ''windows'' GROUP BY COALESCE(NULLIF(ds.health_status, ''''), ''not_reporting'') ORDER BY Y DESC'
WHERE slug = 'dashboard-defender-health-by-status' AND is_system = 1;
