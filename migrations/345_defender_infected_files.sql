-- Preserve the files associated with each Defender protection-history event so
-- responders can inspect them in both endpoint management and Reporting.
ALTER TABLE defender_detections
  ADD COLUMN IF NOT EXISTS infected_files_json JSON NULL AFTER detected_at;

UPDATE reporting_queries
SET sql_query = 'SELECT dd.id, td.hostname AS device, a.name AS asset, dd.threat_name, dd.infected_files_json AS infected_files, dd.severity, dd.status, dd.detected_at, dd.acknowledged_at, dd.resolved_at, dd.ticket_id, dd.detection_uid FROM defender_detections dd INNER JOIN tray_devices td ON td.id = dd.tray_device_id LEFT JOIN assets a ON a.id = td.asset_id WHERE dd.company_id = {{current.company}} ORDER BY dd.detected_at DESC, dd.id DESC'
WHERE slug = 'defender-detections' AND is_system = 1;
