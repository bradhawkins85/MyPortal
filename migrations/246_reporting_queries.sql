-- Reporting feature: super admins author SELECT-only SQL queries that
-- super admins and helpdesk technicians can execute from the Reporting menu.
-- Per-user access is controlled via reporting_query_permissions (super admins
-- always have access regardless of rows in that table).
CREATE TABLE IF NOT EXISTS reporting_queries (
    id INT AUTO_INCREMENT PRIMARY KEY,
    slug VARCHAR(120) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    description TEXT NULL,
    sql_query LONGTEXT NOT NULL,
    is_system TINYINT(1) NOT NULL DEFAULT 0,
    created_by INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_reporting_queries_slug (slug),
    CONSTRAINT fk_reporting_queries_created_by
        FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Per-user permission grants. A row means the user is allowed to run the
-- query in addition to super admins (who always have access).
CREATE TABLE IF NOT EXISTS reporting_query_permissions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    query_id INT NOT NULL,
    user_id INT NOT NULL,
    granted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_reporting_query_user (query_id, user_id),
    KEY idx_reporting_query_perm_user (user_id),
    CONSTRAINT fk_reporting_query_perm_query
        FOREIGN KEY (query_id) REFERENCES reporting_queries(id) ON DELETE CASCADE,
    CONSTRAINT fk_reporting_query_perm_user
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Seed sample queries. Idempotent via INSERT IGNORE on the unique slug.
INSERT IGNORE INTO reporting_queries (slug, name, description, sql_query, is_system)
VALUES
    (
        'unbilled-tickets',
        'Unbilled Tickets',
        'Closed tickets that have billable time entries but have not yet been linked to a Xero invoice.',
        'SELECT t.id AS ticket_id, c.name AS company, t.subject, t.status, COALESCE(SUM(CASE WHEN tr.is_billable = 1 THEN tr.minutes_spent ELSE 0 END), 0) AS billable_minutes, t.closed_at, t.created_at FROM tickets t LEFT JOIN companies c ON c.id = t.company_id LEFT JOIN ticket_replies tr ON tr.ticket_id = t.id WHERE t.xero_invoice_number IS NULL GROUP BY t.id, c.name, t.subject, t.status, t.closed_at, t.created_at HAVING billable_minutes > 0 ORDER BY t.closed_at DESC, t.id DESC',
        1
    ),
    (
        'tech-billable-vs-non-billable-per-day',
        'Tech Billable vs Non-Billable Time Per Day',
        'For each technician and day in the last 30 days, total billable and non-billable minutes recorded on ticket replies.',
        'SELECT DATE(tr.created_at) AS day, COALESCE(u.email, ''(unassigned)'') AS technician, SUM(CASE WHEN tr.is_billable = 1 THEN COALESCE(tr.minutes_spent, 0) ELSE 0 END) AS billable_minutes, SUM(CASE WHEN tr.is_billable = 0 THEN COALESCE(tr.minutes_spent, 0) ELSE 0 END) AS non_billable_minutes FROM ticket_replies tr LEFT JOIN users u ON u.id = tr.author_id WHERE tr.created_at >= (CURRENT_DATE - INTERVAL 30 DAY) AND COALESCE(tr.minutes_spent, 0) > 0 GROUP BY DATE(tr.created_at), u.email ORDER BY day DESC, technician ASC',
        1
    ),
    (
        'count-of-assets-per-company',
        'Count of Assets Per Company',
        'Number of assets registered in MyPortal for each company, highest count first.',
        'SELECT c.id AS company_id, c.name AS company, COUNT(a.id) AS asset_count FROM companies c LEFT JOIN assets a ON a.company_id = c.id GROUP BY c.id, c.name ORDER BY asset_count DESC, c.name ASC',
        1
    );
