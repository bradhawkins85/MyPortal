-- Individual Windows devices can opt out of Defender management when another
-- endpoint protection product is responsible for the device.
ALTER TABLE tray_devices
  ADD COLUMN IF NOT EXISTS defender_managed TINYINT(1) NOT NULL DEFAULT 1;

-- Intentionally excluded devices must not appear unhealthy in system reports.
UPDATE reporting_queries
SET sql_query = REPLACE(sql_query, 'AND LOWER(td.os) = ''windows''', 'AND LOWER(td.os) = ''windows'' AND td.defender_managed = 1')
WHERE is_system = 1
  AND slug IN ('defender-device-status', 'dashboard-defender-devices',
               'dashboard-defender-unhealthy-devices', 'dashboard-defender-health-by-status')
  AND sql_query NOT LIKE '%td.defender_managed%';
