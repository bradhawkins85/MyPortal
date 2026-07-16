-- Per-company default for technician ticket replies. Enabled by default so
-- existing and newly-created companies pre-tick the Billable checkbox.
ALTER TABLE companies
  ADD COLUMN IF NOT EXISTS default_ticket_replies_billable TINYINT(1) NOT NULL DEFAULT 1;

UPDATE companies
SET default_ticket_replies_billable = 1
WHERE default_ticket_replies_billable IS NULL;
