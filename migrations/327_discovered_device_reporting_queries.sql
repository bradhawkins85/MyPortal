-- Add current-company network discovery reports to the Reporting catalogue.
-- These system reports can also be selected for configurable dashboards and
-- Company Reports.  The scanner-filter example deliberately uses LIKE '%'
-- so it returns every scanner until an administrator clones it and replaces
-- the wildcard with a scanner asset name or tray hostname.
INSERT IGNORE INTO reporting_queries (slug, name, description, sql_query, is_system)
VALUES
    (
        'discovered-devices-last-30-days',
        'Discovered Devices - Last 30 Days',
        'Devices first discovered for the current company in the last 30 days, with key inventory, scanner, and MyPortal asset status fields.',
        'SELECT COALESCE(NULLIF(nd.hostname, ''''), nd.ip_address) AS name, nd.ip_address AS local_ip, COALESCE(mv.vendor, nd.vendor) AS vendor, nd.first_seen_at AS first_seen, nd.last_seen_at AS last_seen, COALESCE(scanner_asset.name, td.hostname, ''(unknown scanner)'') AS discovered_by, CASE WHEN nd.matched_asset_id IS NOT NULL THEN CONCAT(''Matched: '', COALESCE(a.name, CONCAT(''Asset #'', nd.matched_asset_id))) WHEN nd.agent_not_required = 1 THEN ''Agent not required'' ELSE ''Agent may be required'' END AS myportal_asset_status, nd.description FROM network_devices nd INNER JOIN tray_devices td ON td.id = nd.scanner_tray_device_id LEFT JOIN assets scanner_asset ON scanner_asset.id = td.asset_id LEFT JOIN assets a ON a.id = nd.matched_asset_id LEFT JOIN mac_vendors mv ON mv.oui_prefix = UPPER(CONCAT(SUBSTRING(nd.mac_address, 1, 2), SUBSTRING(nd.mac_address, 4, 2), SUBSTRING(nd.mac_address, 7, 2))) WHERE nd.company_id = {{current.company}} AND nd.first_seen_at >= (CURRENT_DATE - INTERVAL 30 DAY) ORDER BY nd.first_seen_at DESC, name ASC',
        1
    ),
    (
        'discovered-devices-all',
        'Discovered Devices - All',
        'All devices discovered for the current company, with key inventory, scanner, and MyPortal asset status fields.',
        'SELECT COALESCE(NULLIF(nd.hostname, ''''), nd.ip_address) AS name, nd.ip_address AS local_ip, COALESCE(mv.vendor, nd.vendor) AS vendor, nd.first_seen_at AS first_seen, nd.last_seen_at AS last_seen, COALESCE(scanner_asset.name, td.hostname, ''(unknown scanner)'') AS discovered_by, CASE WHEN nd.matched_asset_id IS NOT NULL THEN CONCAT(''Matched: '', COALESCE(a.name, CONCAT(''Asset #'', nd.matched_asset_id))) WHEN nd.agent_not_required = 1 THEN ''Agent not required'' ELSE ''Agent may be required'' END AS myportal_asset_status, nd.description FROM network_devices nd INNER JOIN tray_devices td ON td.id = nd.scanner_tray_device_id LEFT JOIN assets scanner_asset ON scanner_asset.id = td.asset_id LEFT JOIN assets a ON a.id = nd.matched_asset_id LEFT JOIN mac_vendors mv ON mv.oui_prefix = UPPER(CONCAT(SUBSTRING(nd.mac_address, 1, 2), SUBSTRING(nd.mac_address, 4, 2), SUBSTRING(nd.mac_address, 7, 2))) WHERE nd.company_id = {{current.company}} ORDER BY nd.first_seen_at DESC, name ASC',
        1
    ),
    (
        'discovered-devices-all-details-last-30-days',
        'Discovered Devices - All Details - Last 30 Days',
        'Every stored discovery field plus company, scanner, type, resolved vendor, and matched MyPortal asset details for devices first seen in the last 30 days.',
        'SELECT nd.id, nd.company_id, c.name AS company, nd.scanner_tray_device_id, td.hostname AS scanner_hostname, td.asset_id AS scanner_asset_id, scanner_asset.name AS scanner_asset_name, COALESCE(scanner_asset.name, td.hostname, ''(unknown scanner)'') AS discovered_by, nd.wan_ip, nd.ip_address AS local_ip, nd.mac_address, nd.hostname AS name, nd.vendor AS reported_vendor, mv.vendor AS mac_vendor, COALESCE(mv.vendor, nd.vendor) AS vendor, nd.os_details, nd.open_ports, nd.first_seen_at AS first_seen, nd.last_seen_at AS last_seen, nd.matched_asset_id, a.name AS matched_asset_name, a.status AS matched_asset_status, CASE WHEN nd.matched_asset_id IS NOT NULL THEN CONCAT(''Matched: '', COALESCE(a.name, CONCAT(''Asset #'', nd.matched_asset_id))) WHEN nd.agent_not_required = 1 THEN ''Agent not required'' ELSE ''Agent may be required'' END AS myportal_asset_status, nd.state, nd.device_type_id, dt.name AS device_type, nd.description, nd.agent_not_required FROM network_devices nd INNER JOIN companies c ON c.id = nd.company_id INNER JOIN tray_devices td ON td.id = nd.scanner_tray_device_id LEFT JOIN assets scanner_asset ON scanner_asset.id = td.asset_id LEFT JOIN assets a ON a.id = nd.matched_asset_id LEFT JOIN network_device_types dt ON dt.id = nd.device_type_id LEFT JOIN mac_vendors mv ON mv.oui_prefix = UPPER(CONCAT(SUBSTRING(nd.mac_address, 1, 2), SUBSTRING(nd.mac_address, 4, 2), SUBSTRING(nd.mac_address, 7, 2))) WHERE nd.company_id = {{current.company}} AND nd.first_seen_at >= (CURRENT_DATE - INTERVAL 30 DAY) ORDER BY nd.first_seen_at DESC, name ASC',
        1
    ),
    (
        'discovered-devices-all-details',
        'Discovered Devices - All Details',
        'Every stored discovery field plus company, scanner, type, resolved vendor, and matched MyPortal asset details for all devices in the current company.',
        'SELECT nd.id, nd.company_id, c.name AS company, nd.scanner_tray_device_id, td.hostname AS scanner_hostname, td.asset_id AS scanner_asset_id, scanner_asset.name AS scanner_asset_name, COALESCE(scanner_asset.name, td.hostname, ''(unknown scanner)'') AS discovered_by, nd.wan_ip, nd.ip_address AS local_ip, nd.mac_address, nd.hostname AS name, nd.vendor AS reported_vendor, mv.vendor AS mac_vendor, COALESCE(mv.vendor, nd.vendor) AS vendor, nd.os_details, nd.open_ports, nd.first_seen_at AS first_seen, nd.last_seen_at AS last_seen, nd.matched_asset_id, a.name AS matched_asset_name, a.status AS matched_asset_status, CASE WHEN nd.matched_asset_id IS NOT NULL THEN CONCAT(''Matched: '', COALESCE(a.name, CONCAT(''Asset #'', nd.matched_asset_id))) WHEN nd.agent_not_required = 1 THEN ''Agent not required'' ELSE ''Agent may be required'' END AS myportal_asset_status, nd.state, nd.device_type_id, dt.name AS device_type, nd.description, nd.agent_not_required FROM network_devices nd INNER JOIN companies c ON c.id = nd.company_id INNER JOIN tray_devices td ON td.id = nd.scanner_tray_device_id LEFT JOIN assets scanner_asset ON scanner_asset.id = td.asset_id LEFT JOIN assets a ON a.id = nd.matched_asset_id LEFT JOIN network_device_types dt ON dt.id = nd.device_type_id LEFT JOIN mac_vendors mv ON mv.oui_prefix = UPPER(CONCAT(SUBSTRING(nd.mac_address, 1, 2), SUBSTRING(nd.mac_address, 4, 2), SUBSTRING(nd.mac_address, 7, 2))) WHERE nd.company_id = {{current.company}} ORDER BY nd.first_seen_at DESC, name ASC',
        1
    ),
    (
        'discovered-devices-by-scanner-example',
        'Discovered Devices by Scanner - Filter Example',
        'Example scanner filter for the current company. Clone this report and replace the % wildcard in the discovered-by LIKE filter with a scanner asset name or tray hostname.',
        'SELECT COALESCE(NULLIF(nd.hostname, ''''), nd.ip_address) AS name, nd.ip_address AS local_ip, COALESCE(mv.vendor, nd.vendor) AS vendor, nd.first_seen_at AS first_seen, nd.last_seen_at AS last_seen, COALESCE(scanner_asset.name, td.hostname, ''(unknown scanner)'') AS discovered_by, CASE WHEN nd.matched_asset_id IS NOT NULL THEN CONCAT(''Matched: '', COALESCE(a.name, CONCAT(''Asset #'', nd.matched_asset_id))) WHEN nd.agent_not_required = 1 THEN ''Agent not required'' ELSE ''Agent may be required'' END AS myportal_asset_status, nd.description FROM network_devices nd INNER JOIN tray_devices td ON td.id = nd.scanner_tray_device_id LEFT JOIN assets scanner_asset ON scanner_asset.id = td.asset_id LEFT JOIN assets a ON a.id = nd.matched_asset_id LEFT JOIN mac_vendors mv ON mv.oui_prefix = UPPER(CONCAT(SUBSTRING(nd.mac_address, 1, 2), SUBSTRING(nd.mac_address, 4, 2), SUBSTRING(nd.mac_address, 7, 2))) WHERE nd.company_id = {{current.company}} AND nd.first_seen_at >= (CURRENT_DATE - INTERVAL 30 DAY) AND COALESCE(scanner_asset.name, td.hostname, '''') LIKE ''%'' ORDER BY nd.first_seen_at DESC, name ASC',
        1
    );
