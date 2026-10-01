-- Ensure the company summary exists on installations that already recorded an
-- older version of migration 302. That version renamed the ticket-level report
-- instead of inserting this report, so later UPDATE-only migrations could not
-- make it appear in the Reporting dropdown.
INSERT IGNORE INTO reporting_queries (slug, name, description, sql_query, is_system)
VALUES (
    'unbilled-tickets-by-company',
    'Unbilled Tickets By Company',
    'Unbilled billable ticket time by company and labour type, including the number of tickets represented in each total.',
    'SELECT c.id AS company_id, COALESCE(c.name, ''(no company)'') AS company, COALESCE(NULLIF(TRIM(lt.name), ''''), ''(unspecified)'') AS labour_type, COUNT(DISTINCT t.id) AS ticket_count, SUM(tr.minutes_spent) AS billable_minutes FROM ticket_replies tr JOIN tickets t ON t.id = tr.ticket_id LEFT JOIN companies c ON c.id = t.company_id LEFT JOIN ticket_labour_types lt ON lt.id = tr.labour_type_id WHERE tr.is_billable = 1 AND tr.minutes_spent IS NOT NULL AND tr.minutes_spent > 0 AND NOT EXISTS (SELECT 1 FROM ticket_billed_time_entries bte WHERE bte.reply_id = tr.id) GROUP BY c.id, c.name, lt.id, lt.name ORDER BY company ASC, labour_type ASC',
    1
);

-- Also repair the definition if a row with this slug already exists.
UPDATE reporting_queries
SET
    name = 'Unbilled Tickets By Company',
    description = 'Unbilled billable ticket time by company and labour type, including the number of tickets represented in each total.',
    sql_query = 'SELECT c.id AS company_id, COALESCE(c.name, ''(no company)'') AS company, COALESCE(NULLIF(TRIM(lt.name), ''''), ''(unspecified)'') AS labour_type, COUNT(DISTINCT t.id) AS ticket_count, SUM(tr.minutes_spent) AS billable_minutes FROM ticket_replies tr JOIN tickets t ON t.id = tr.ticket_id LEFT JOIN companies c ON c.id = t.company_id LEFT JOIN ticket_labour_types lt ON lt.id = tr.labour_type_id WHERE tr.is_billable = 1 AND tr.minutes_spent IS NOT NULL AND tr.minutes_spent > 0 AND NOT EXISTS (SELECT 1 FROM ticket_billed_time_entries bte WHERE bte.reply_id = tr.id) GROUP BY c.id, c.name, lt.id, lt.name ORDER BY company ASC, labour_type ASC',
    is_system = 1
WHERE slug = 'unbilled-tickets-by-company';
