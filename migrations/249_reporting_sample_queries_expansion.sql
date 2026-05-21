-- Add additional system reporting queries across core app functions.
-- Idempotent via INSERT IGNORE on the unique slug.
INSERT IGNORE INTO reporting_queries (slug, name, description, sql_query, is_system)
VALUES
    (
        'tickets-open-by-priority',
        'Tickets: Open Backlog by Priority',
        'Open ticket counts grouped by priority so teams can quickly identify urgent backlog trends.',
        'SELECT COALESCE(t.priority, ''(unspecified)'') AS priority, COUNT(*) AS open_ticket_count FROM tickets t WHERE t.status IN (''open'', ''in_progress'', ''pending'') GROUP BY COALESCE(t.priority, ''(unspecified)'') ORDER BY open_ticket_count DESC, priority ASC',
        1
    ),
    (
        'tickets-resolution-time-last-30-days',
        'Tickets: Average Resolution Time (Last 30 Days)',
        'Average ticket resolution time in hours for tickets closed in the last 30 days, grouped by company.',
        'SELECT COALESCE(c.name, ''(unassigned company)'') AS company, COUNT(*) AS tickets_closed, ROUND(AVG(GREATEST(TIMESTAMPDIFF(MINUTE, t.created_at, t.closed_at), 0)) / 60, 2) AS avg_resolution_hours FROM tickets t LEFT JOIN companies c ON c.id = t.company_id WHERE t.closed_at IS NOT NULL AND t.closed_at >= (CURRENT_DATE - INTERVAL 30 DAY) GROUP BY COALESCE(c.id, 0), COALESCE(c.name, ''(unassigned company)'') ORDER BY avg_resolution_hours DESC, company ASC',
        1
    ),
    (
        'tickets-open-workload-by-technician',
        'Tickets: Open Workload by Technician',
        'Current open ticket count assigned to each technician, including unassigned tickets.',
        'SELECT COALESCE(u.email, ''(unassigned)'') AS technician, COUNT(*) AS open_ticket_count FROM tickets t LEFT JOIN users u ON u.id = t.assigned_user_id WHERE t.status IN (''open'', ''in_progress'', ''pending'') GROUP BY u.id, u.email ORDER BY open_ticket_count DESC, technician ASC',
        1
    ),
    (
        'companies-user-and-asset-footprint',
        'Companies: User and Asset Footprint',
        'Per-company counts of active users and registered assets for capacity and onboarding planning.',
        'SELECT c.id AS company_id, c.name AS company, COALESCE(u_counts.active_user_count, 0) AS active_user_count, COALESCE(a_counts.asset_count, 0) AS asset_count FROM companies c LEFT JOIN (SELECT u.company_id, COUNT(*) AS active_user_count FROM users u WHERE u.is_active = 1 GROUP BY u.company_id) AS u_counts ON u_counts.company_id = c.id LEFT JOIN (SELECT a.company_id, COUNT(*) AS asset_count FROM assets a GROUP BY a.company_id) AS a_counts ON a_counts.company_id = c.id ORDER BY c.name ASC',
        1
    ),
    (
        'users-inactive-by-company',
        'Users: Inactive Accounts by Company',
        'Inactive user accounts grouped by company to support access cleanup and security reviews.',
        'SELECT COALESCE(c.name, ''(no company)'') AS company, COUNT(*) AS inactive_user_count FROM users u LEFT JOIN companies c ON c.id = u.company_id WHERE u.is_active = 0 GROUP BY COALESCE(c.id, 0), COALESCE(c.name, ''(no company)'') ORDER BY inactive_user_count DESC, company ASC',
        1
    ),
    (
        'users-email-domain-distribution',
        'Users: Email Domain Distribution',
        'User count by email domain to help identify tenant segmentation and external account usage.',
        'SELECT LOWER(SUBSTRING_INDEX(u.email, ''@'', -1)) AS email_domain, COUNT(*) AS user_count FROM users u GROUP BY LOWER(SUBSTRING_INDEX(u.email, ''@'', -1)) ORDER BY user_count DESC, email_domain ASC',
        1
    ),
    (
        'assets-by-status-and-type',
        'Assets: Status by Type',
        'Asset counts broken down by type and status to support lifecycle management and audits.',
        'SELECT COALESCE(NULLIF(TRIM(a.type), ''''), ''(unspecified)'') AS asset_type, COALESCE(NULLIF(TRIM(a.status), ''''), ''(unspecified)'') AS asset_status, COUNT(*) AS asset_count FROM assets a GROUP BY COALESCE(NULLIF(TRIM(a.type), ''''), ''(unspecified)''), COALESCE(NULLIF(TRIM(a.status), ''''), ''(unspecified)'') ORDER BY asset_count DESC, asset_type ASC, asset_status ASC',
        1
    ),
    (
        'licenses-expiring-next-90-days',
        'Licenses: Expiring in Next 90 Days',
        'Licenses with upcoming expiry dates in the next 90 days, including company and seat counts.',
        'SELECT COALESCE(c.name, ''(no company)'') AS company, l.name AS license_name, l.platform, l.count AS seat_count, l.expiry_date FROM licenses l LEFT JOIN companies c ON c.id = l.company_id WHERE l.expiry_date IS NOT NULL AND l.expiry_date >= CURRENT_DATE AND l.expiry_date < (CURRENT_DATE + INTERVAL 90 DAY) ORDER BY l.expiry_date ASC, company ASC, l.name ASC',
        1
    ),
    (
        'automations-failure-summary-last-14-days',
        'Automations: Failure Summary (Last 14 Days)',
        'Failed automation runs in the last 14 days grouped by automation for reliability monitoring.',
        'SELECT a.name AS automation, a.kind, COUNT(*) AS failed_runs, MAX(ar.started_at) AS last_failed_at FROM automation_runs ar JOIN automations a ON a.id = ar.automation_id WHERE ar.status = ''failed'' AND ar.started_at >= (CURRENT_DATE - INTERVAL 14 DAY) GROUP BY a.id, a.name, a.kind ORDER BY failed_runs DESC, last_failed_at DESC, a.name ASC',
        1
    );
