ALTER TABLE scheduled_invoices
    ADD COLUMN reminder_ticket_id INT NULL AFTER status,
    ADD COLUMN reminder_reply_id INT NULL AFTER reminder_ticket_id,
    ADD COLUMN reminder_sent_at DATETIME(6) NULL AFTER reminder_reply_id,
    ADD COLUMN reminder_email_sent_at DATETIME(6) NULL AFTER reminder_sent_at,
    ADD COLUMN reminder_error TEXT NULL AFTER reminder_email_sent_at,
    ADD COLUMN invoice_id INT NULL AFTER reminder_error,
    ADD COLUMN invoice_number VARCHAR(255) NULL AFTER invoice_id,
    ADD COLUMN invoice_sent_at DATETIME(6) NULL AFTER invoice_number,
    ADD COLUMN invoice_error TEXT NULL AFTER invoice_sent_at;

CREATE INDEX idx_scheduled_invoices_reminder_ticket ON scheduled_invoices(reminder_ticket_id);
CREATE INDEX idx_scheduled_invoices_invoice_id ON scheduled_invoices(invoice_id);
