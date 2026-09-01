-- Track Windows Defender Firewall state independently for each network profile.
ALTER TABLE defender_device_status
  ADD COLUMN IF NOT EXISTS firewall_domain_enabled TINYINT(1) NULL AFTER tamper_protection_enabled,
  ADD COLUMN IF NOT EXISTS firewall_private_enabled TINYINT(1) NULL AFTER firewall_domain_enabled,
  ADD COLUMN IF NOT EXISTS firewall_public_enabled TINYINT(1) NULL AFTER firewall_private_enabled;

UPDATE reporting_queries
SET sql_query = 'SELECT td.hostname AS device, a.name AS asset, COALESCE(ds.health_status, ''not_reporting'') AS health_status, COALESCE(ds.antivirus_enabled, 0) AS antivirus_enabled, COALESCE(ds.realtime_protection_enabled, 0) AS realtime_protection_enabled, COALESCE(ds.tamper_protection_enabled, 0) AS tamper_protection_enabled, ds.firewall_domain_enabled, ds.firewall_private_enabled, ds.firewall_public_enabled, ds.signatures_updated_at, ds.last_scan_at, COALESCE(ds.threat_count, 0) AS active_threats, ds.updated_at AS status_updated_at, td.last_seen_utc AS device_last_seen FROM tray_devices td LEFT JOIN assets a ON a.id = td.asset_id LEFT JOIN defender_device_status ds ON ds.tray_device_id = td.id WHERE td.company_id = {{current.company}} AND td.status = ''active'' AND LOWER(td.os) = ''windows'' ORDER BY device ASC'
WHERE slug = 'defender-device-status' AND is_system = 1;
