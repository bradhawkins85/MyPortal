ALTER TABLE company_recurring_invoice_items
  ADD COLUMN IF NOT EXISTS billing_frequency VARCHAR(32) NOT NULL DEFAULT 'every_run' AFTER active,
  ADD COLUMN IF NOT EXISTS billing_interval INT NULL AFTER billing_frequency,
  ADD COLUMN IF NOT EXISTS start_date DATE NULL AFTER billing_interval,
  ADD COLUMN IF NOT EXISTS end_date DATE NULL AFTER start_date,
  ADD COLUMN IF NOT EXISTS last_billed_at DATETIME(6) NULL AFTER end_date;

CREATE INDEX IF NOT EXISTS idx_recurring_invoice_items_schedule
  ON company_recurring_invoice_items(active, billing_frequency, start_date, end_date, last_billed_at);
