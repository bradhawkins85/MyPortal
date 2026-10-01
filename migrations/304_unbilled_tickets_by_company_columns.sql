-- Keep the Unbilled Tickets By Company system report consistent on both new
-- and upgraded installations. Include the company identifier so companies
-- with the same display name remain distinguishable in exports.
UPDATE reporting_queries
SET
    name = 'Unbilled Tickets By Company',
    description = 'Unbilled billable ticket time by company and labour type, including the number of tickets represented in each total.',
    sql_query = 'SELECT c.id AS company_id, COALESCE(c.name, ''(no company)'') AS company, COALESCE(NULLIF(TRIM(lt.name), ''''), ''(unspecified)'') AS labour_type, COUNT(DISTINCT t.id) AS ticket_count, SUM(tr.minutes_spent) AS billable_minutes FROM ticket_replies tr JOIN tickets t ON t.id = tr.ticket_id LEFT JOIN companies c ON c.id = t.company_id LEFT JOIN ticket_labour_types lt ON lt.id = tr.labour_type_id WHERE tr.is_billable = 1 AND tr.minutes_spent IS NOT NULL AND tr.minutes_spent > 0 AND NOT EXISTS (SELECT 1 FROM ticket_billed_time_entries bte WHERE bte.reply_id = tr.id) GROUP BY c.id, c.name, lt.id, lt.name ORDER BY company ASC, labour_type ASC'
WHERE slug = 'unbilled-tickets-by-company'
  AND is_system = 1;
