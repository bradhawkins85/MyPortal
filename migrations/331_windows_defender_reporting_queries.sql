-- Add current-company Windows Defender reports to the Reporting catalogue.
-- The dashboard entries provide both countable row sets for stat panels and
-- explicit X/Y result sets for graph panels.
INSERT IGNORE INTO reporting_queries (slug, name, description, sql_query, is_system)
VALUES
    (
        'defender-device-status',
        'Windows Defender - Device Status',
        'Windows Defender protection, signature, scan, and threat status for active tray devices in the current company.',
        'SELECT td.hostname AS device, a.name AS asset, COALESCE(ds.health_status, ''not_reporting'') AS health_status, COALESCE(ds.antivirus_enabled, 0) AS antivirus_enabled, COALESCE(ds.realtime_protection_enabled, 0) AS realtime_protection_enabled, COALESCE(ds.tamper_protection_enabled, 0) AS tamper_protection_enabled, ds.signatures_updated_at, ds.last_scan_at, COALESCE(ds.threat_count, 0) AS active_threats, ds.updated_at AS status_updated_at, td.last_seen_utc AS device_last_seen FROM tray_devices td LEFT JOIN assets a ON a.id = td.asset_id LEFT JOIN defender_device_status ds ON ds.tray_device_id = td.id WHERE td.company_id = {{current.company}} AND td.status = ''active'' ORDER BY device ASC',
        1
    ),
    (
        'defender-detections',
        'Windows Defender - Detections',
        'Windows Defender detections and their response workflow for the current company.',
        'SELECT dd.id, td.hostname AS device, a.name AS asset, dd.threat_name, dd.severity, dd.status, dd.detected_at, dd.acknowledged_at, dd.resolved_at, dd.ticket_id, dd.detection_uid FROM defender_detections dd INNER JOIN tray_devices td ON td.id = dd.tray_device_id LEFT JOIN assets a ON a.id = td.asset_id WHERE dd.company_id = {{current.company}} ORDER BY dd.detected_at DESC, dd.id DESC',
        1
    ),
    (
        'dashboard-defender-devices',
        'Dashboard - Windows Defender devices',
        'Dashboard panel: Active tray devices reporting Windows Defender status for the current company.',
        'SELECT td.id, td.hostname FROM tray_devices td INNER JOIN defender_device_status ds ON ds.tray_device_id = td.id WHERE td.company_id = {{current.company}} AND td.status = ''active''',
        1
    ),
    (
        'dashboard-defender-unhealthy-devices',
        'Dashboard - Windows Defender unhealthy devices',
        'Dashboard panel: Active devices that are not reporting healthy Windows Defender protection.',
        'SELECT td.id, td.hostname, COALESCE(ds.health_status, ''not_reporting'') AS health_status FROM tray_devices td LEFT JOIN defender_device_status ds ON ds.tray_device_id = td.id WHERE td.company_id = {{current.company}} AND td.status = ''active'' AND (ds.tray_device_id IS NULL OR LOWER(COALESCE(ds.health_status, ''unknown'')) <> ''healthy'' OR ds.antivirus_enabled = 0 OR ds.realtime_protection_enabled = 0)',
        1
    ),
    (
        'dashboard-defender-health-by-status',
        'Dashboard - Windows Defender health by status',
        'Dashboard graph: Active devices grouped by their reported Windows Defender health status.',
        'SELECT COALESCE(NULLIF(ds.health_status, ''''), ''not_reporting'') AS X, COUNT(*) AS Y FROM tray_devices td LEFT JOIN defender_device_status ds ON ds.tray_device_id = td.id WHERE td.company_id = {{current.company}} AND td.status = ''active'' GROUP BY COALESCE(NULLIF(ds.health_status, ''''), ''not_reporting'') ORDER BY Y DESC',
        1
    ),
    (
        'dashboard-defender-active-detections-by-severity',
        'Dashboard - Windows Defender active detections by severity',
        'Dashboard graph: Active Windows Defender detections grouped by severity for the current company.',
        'SELECT COALESCE(NULLIF(dd.severity, ''''), ''unknown'') AS X, COUNT(*) AS Y FROM defender_detections dd WHERE dd.company_id = {{current.company}} AND dd.status = ''active'' GROUP BY COALESCE(NULLIF(dd.severity, ''''), ''unknown'') ORDER BY Y DESC',
        1
    );
