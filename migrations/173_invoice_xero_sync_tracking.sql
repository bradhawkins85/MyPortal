ALTER TABLE invoices
    ADD COLUMN IF NOT EXISTS xero_invoice_id VARCHAR(64) NULL AFTER status,
    ADD COLUMN IF NOT EXISTS synced_to_xero_at DATETIME(6) NULL AFTER xero_invoice_id;

CREATE INDEX IF NOT EXISTS idx_invoices_xero_invoice_id ON invoices(xero_invoice_id);
