ALTER TABLE invoices
    ADD COLUMN IF NOT EXISTS approval_required TINYINT(1) NOT NULL DEFAULT 0 AFTER status,
    ADD COLUMN IF NOT EXISTS approved_by INT NULL AFTER approval_required,
    ADD COLUMN IF NOT EXISTS approved_at DATETIME(6) NULL AFTER approved_by,
    ADD COLUMN IF NOT EXISTS billing_adjustment_type VARCHAR(32) NULL AFTER approved_at,
    ADD COLUMN IF NOT EXISTS billing_adjustment_amount DECIMAL(12,2) NULL AFTER billing_adjustment_type,
    ADD COLUMN IF NOT EXISTS billing_adjustment_reason VARCHAR(255) NULL AFTER billing_adjustment_amount,
    ADD COLUMN IF NOT EXISTS xero_sync_error VARCHAR(500) NULL AFTER synced_to_xero_at,
    ADD COLUMN IF NOT EXISTS xero_sync_attempted_at DATETIME(6) NULL AFTER xero_sync_error;

CREATE INDEX IF NOT EXISTS idx_invoices_approval_required ON invoices(approval_required, approved_at);
CREATE INDEX IF NOT EXISTS idx_invoices_xero_sync_attempted_at ON invoices(xero_sync_attempted_at);

INSERT IGNORE INTO reporting_queries (slug, name, description, sql_query, is_system)
VALUES (
    'revenue-leakage-unbilled-time',
    'Revenue Leakage - Unbilled Time',
    'Remaining unbilled ticket time by age, company, and technician so operators can follow up on billing leakage.',
    'SELECT c.id AS company_id, COALESCE(c.name, ''(no company)'') AS company, COALESCE(NULLIF(TRIM(CONCAT(COALESCE(u.first_name, ''''), '' '', COALESCE(u.last_name, ''''))), ''''), NULLIF(TRIM(u.email), ''''), ''(unassigned)'') AS technician, CASE WHEN TIMESTAMPDIFF(DAY, COALESCE(t.closed_at, t.created_at), CURRENT_TIMESTAMP) >= 30 THEN ''30+ days'' WHEN TIMESTAMPDIFF(DAY, COALESCE(t.closed_at, t.created_at), CURRENT_TIMESTAMP) >= 14 THEN ''14-29 days'' WHEN TIMESTAMPDIFF(DAY, COALESCE(t.closed_at, t.created_at), CURRENT_TIMESTAMP) >= 7 THEN ''7-13 days'' ELSE ''0-6 days'' END AS age_bucket, COUNT(DISTINCT t.id) AS ticket_count, COUNT(DISTINCT tr.id) AS reply_count, SUM(tr.minutes_spent - COALESCE(billed.billed_minutes, 0)) AS unbilled_minutes, ROUND(SUM((tr.minutes_spent - COALESCE(billed.billed_minutes, 0)) * COALESCE(lt.rate, 0)), 2) AS unbilled_amount FROM ticket_replies tr JOIN tickets t ON t.id = tr.ticket_id LEFT JOIN companies c ON c.id = t.company_id LEFT JOIN users u ON u.id = tr.author_id LEFT JOIN ticket_labour_types lt ON lt.id = tr.labour_type_id LEFT JOIN (SELECT reply_id, SUM(minutes_billed) AS billed_minutes FROM ticket_billed_time_entries GROUP BY reply_id) billed ON billed.reply_id = tr.id WHERE tr.is_billable = 1 AND tr.minutes_spent IS NOT NULL AND tr.minutes_spent > COALESCE(billed.billed_minutes, 0) GROUP BY c.id, c.name, technician, age_bucket ORDER BY unbilled_amount DESC, unbilled_minutes DESC, company ASC, technician ASC',
    1
);

UPDATE reporting_queries
SET
    name = 'Revenue Leakage - Unbilled Time',
    description = 'Remaining unbilled ticket time by age, company, and technician so operators can follow up on billing leakage.',
    sql_query = 'SELECT c.id AS company_id, COALESCE(c.name, ''(no company)'') AS company, COALESCE(NULLIF(TRIM(CONCAT(COALESCE(u.first_name, ''''), '' '', COALESCE(u.last_name, ''''))), ''''), NULLIF(TRIM(u.email), ''''), ''(unassigned)'') AS technician, CASE WHEN TIMESTAMPDIFF(DAY, COALESCE(t.closed_at, t.created_at), CURRENT_TIMESTAMP) >= 30 THEN ''30+ days'' WHEN TIMESTAMPDIFF(DAY, COALESCE(t.closed_at, t.created_at), CURRENT_TIMESTAMP) >= 14 THEN ''14-29 days'' WHEN TIMESTAMPDIFF(DAY, COALESCE(t.closed_at, t.created_at), CURRENT_TIMESTAMP) >= 7 THEN ''7-13 days'' ELSE ''0-6 days'' END AS age_bucket, COUNT(DISTINCT t.id) AS ticket_count, COUNT(DISTINCT tr.id) AS reply_count, SUM(tr.minutes_spent - COALESCE(billed.billed_minutes, 0)) AS unbilled_minutes, ROUND(SUM((tr.minutes_spent - COALESCE(billed.billed_minutes, 0)) * COALESCE(lt.rate, 0)), 2) AS unbilled_amount FROM ticket_replies tr JOIN tickets t ON t.id = tr.ticket_id LEFT JOIN companies c ON c.id = t.company_id LEFT JOIN users u ON u.id = tr.author_id LEFT JOIN ticket_labour_types lt ON lt.id = tr.labour_type_id LEFT JOIN (SELECT reply_id, SUM(minutes_billed) AS billed_minutes FROM ticket_billed_time_entries GROUP BY reply_id) billed ON billed.reply_id = tr.id WHERE tr.is_billable = 1 AND tr.minutes_spent IS NOT NULL AND tr.minutes_spent > COALESCE(billed.billed_minutes, 0) GROUP BY c.id, c.name, technician, age_bucket ORDER BY unbilled_amount DESC, unbilled_minutes DESC, company ASC, technician ASC',
    is_system = 1
WHERE slug = 'revenue-leakage-unbilled-time';
