-- Add current-company Windows 10 computer reports to the Reporting catalogue.
-- Each report uses a rolling last-sync window and deliberately selects only
-- the requested inventory fields.
INSERT IGNORE INTO reporting_queries (slug, name, description, sql_query, is_system)
VALUES
    (
        'windows-10-computers-synced-last-30-days',
        'Windows 10 Computers - Synced Last 30 Days',
        'Windows 10 computers for the current company that synced within the last 30 days.',
        'SELECT a.name AS computer_name, a.serial_number, a.os_name, a.last_sync, a.last_user FROM assets a WHERE a.company_id = {{current.company}} AND LOWER(TRIM(COALESCE(a.os_name, ''''))) LIKE ''%windows 10%'' AND a.last_sync IS NOT NULL AND a.last_sync >= (CURRENT_DATE - INTERVAL 30 DAY) ORDER BY a.last_sync DESC, a.name ASC',
        1
    ),
    (
        'windows-10-computers-synced-last-60-days',
        'Windows 10 Computers - Synced Last 60 Days',
        'Windows 10 computers for the current company that synced within the last 60 days.',
        'SELECT a.name AS computer_name, a.serial_number, a.os_name, a.last_sync, a.last_user FROM assets a WHERE a.company_id = {{current.company}} AND LOWER(TRIM(COALESCE(a.os_name, ''''))) LIKE ''%windows 10%'' AND a.last_sync IS NOT NULL AND a.last_sync >= (CURRENT_DATE - INTERVAL 60 DAY) ORDER BY a.last_sync DESC, a.name ASC',
        1
    ),
    (
        'windows-10-computers-synced-last-90-days',
        'Windows 10 Computers - Synced Last 90 Days',
        'Windows 10 computers for the current company that synced within the last 90 days.',
        'SELECT a.name AS computer_name, a.serial_number, a.os_name, a.last_sync, a.last_user FROM assets a WHERE a.company_id = {{current.company}} AND LOWER(TRIM(COALESCE(a.os_name, ''''))) LIKE ''%windows 10%'' AND a.last_sync IS NOT NULL AND a.last_sync >= (CURRENT_DATE - INTERVAL 90 DAY) ORDER BY a.last_sync DESC, a.name ASC',
        1
    );
