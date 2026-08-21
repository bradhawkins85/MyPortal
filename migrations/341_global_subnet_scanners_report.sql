-- Add a portal-wide inventory of devices nominated as subnet scanners.
-- A LEFT JOIN keeps enabled scanners visible before their first successful scan;
-- subsequent rows show each subnet recorded for that scanner.
INSERT IGNORE INTO reporting_queries (slug, name, description, sql_query, is_system)
VALUES (
    'global-subnet-scanners',
    'Subnet Scanners - All Companies',
    'All enabled subnet scanners across every company, including the nominated device and each subnet it has scanned.',
    'SELECT c.name AS company, COALESCE(NULLIF(a.name, ''''), NULLIF(td.hostname, ''''), CONCAT(''Tray device #'', td.id)) AS scanner_device, COALESCE(nss.subnet, ''No subnet recorded'') AS scanned_subnet FROM tray_devices td INNER JOIN companies c ON c.id = td.company_id LEFT JOIN assets a ON a.id = td.asset_id LEFT JOIN network_scan_subnets nss ON nss.scanner_tray_device_id = td.id AND nss.company_id = td.company_id WHERE td.network_scanner_enabled = 1 ORDER BY c.name ASC, scanner_device ASC, nss.subnet ASC',
    1
);
