-- Add three system reporting queries for billed tickets and per-company billing totals.
-- Idempotent via INSERT IGNORE on the unique slug.
INSERT IGNORE INTO reporting_queries (slug, name, description, sql_query, is_system)
VALUES
    (
        'billed-tickets',
        'Billed Tickets',
        'Tickets that have been billed via Xero, showing the invoice number, total minutes billed, and calculated amount billed per ticket.',
        'SELECT t.id AS ticket_id, c.name AS company, t.subject, t.status, t.xero_invoice_number, COALESCE(SUM(bte.minutes_billed), 0) AS total_minutes_billed, ROUND(COALESCE(SUM(bte.minutes_billed * COALESCE(lt.rate, 0) / 60.0), 0), 2) AS total_amount_billed, t.billed_at, t.closed_at FROM tickets t LEFT JOIN companies c ON c.id = t.company_id LEFT JOIN ticket_billed_time_entries bte ON bte.ticket_id = t.id LEFT JOIN ticket_labour_types lt ON lt.id = bte.labour_type_id WHERE t.xero_invoice_number IS NOT NULL GROUP BY t.id, c.name, t.subject, t.status, t.xero_invoice_number, t.billed_at, t.closed_at ORDER BY t.billed_at DESC, t.id DESC',
        1
    ),
    (
        'billable-amount-per-company-current-month',
        'Billable Amount Per Company – Current Month',
        'Total billed amount per company for the current calendar month, based on time entries recorded in ticket_billed_time_entries.',
        'SELECT c.name AS company, COUNT(DISTINCT bte.ticket_id) AS tickets_billed, SUM(bte.minutes_billed) AS total_minutes_billed, ROUND(SUM(bte.minutes_billed * COALESCE(lt.rate, 0) / 60.0), 2) AS total_amount_billed FROM ticket_billed_time_entries bte JOIN tickets t ON t.id = bte.ticket_id JOIN companies c ON c.id = t.company_id LEFT JOIN ticket_labour_types lt ON lt.id = bte.labour_type_id WHERE YEAR(bte.billed_at) = YEAR(CURRENT_DATE) AND MONTH(bte.billed_at) = MONTH(CURRENT_DATE) GROUP BY c.id, c.name ORDER BY total_amount_billed DESC, c.name ASC',
        1
    ),
    (
        'billable-amount-per-company-previous-month',
        'Billable Amount Per Company – Previous Month',
        'Total billed amount per company for the previous calendar month, based on time entries recorded in ticket_billed_time_entries.',
        'SELECT c.name AS company, COUNT(DISTINCT bte.ticket_id) AS tickets_billed, SUM(bte.minutes_billed) AS total_minutes_billed, ROUND(SUM(bte.minutes_billed * COALESCE(lt.rate, 0) / 60.0), 2) AS total_amount_billed FROM ticket_billed_time_entries bte JOIN tickets t ON t.id = bte.ticket_id JOIN companies c ON c.id = t.company_id LEFT JOIN ticket_labour_types lt ON lt.id = bte.labour_type_id WHERE YEAR(bte.billed_at) = YEAR(DATE_SUB(CURRENT_DATE, INTERVAL 1 MONTH)) AND MONTH(bte.billed_at) = MONTH(DATE_SUB(CURRENT_DATE, INTERVAL 1 MONTH)) GROUP BY c.id, c.name ORDER BY total_amount_billed DESC, c.name ASC',
        1
    );
