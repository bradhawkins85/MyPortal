-- Restore the original ticket-level report for installations that applied an
-- earlier version of migration 302, which renamed this system seed in place.
-- The company summary now has its own slug and is inserted by migration 302.
UPDATE reporting_queries
SET
    name = 'Unbilled Tickets',
    description = 'Closed tickets that have billable time entries but have not yet been linked to a Xero invoice.',
    sql_query = 'SELECT t.id AS ticket_id, c.name AS company, t.subject, t.status, COALESCE(SUM(CASE WHEN tr.is_billable = 1 THEN tr.minutes_spent ELSE 0 END), 0) AS billable_minutes, t.closed_at, t.created_at FROM tickets t LEFT JOIN companies c ON c.id = t.company_id LEFT JOIN ticket_replies tr ON tr.ticket_id = t.id WHERE t.xero_invoice_number IS NULL GROUP BY t.id, c.name, t.subject, t.status, t.closed_at, t.created_at HAVING billable_minutes > 0 ORDER BY t.closed_at DESC, t.id DESC'
WHERE slug = 'unbilled-tickets'
  AND is_system = 1;
