-- Add Xero invoice tracking to tickets table
ALTER TABLE tickets
    ADD COLUMN xero_invoice_number VARCHAR(64) NULL AFTER closed_at,
    ADD COLUMN billed_at DATETIME(6) NULL AFTER xero_invoice_number;

CREATE INDEX idx_tickets_xero_invoice ON tickets(xero_invoice_number);
CREATE INDEX idx_tickets_billed_at ON tickets(billed_at);

-- Create table to track which time entries have been billed
CREATE TABLE ticket_billed_time_entries (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    ticket_id INT NOT NULL,
    reply_id INT NOT NULL,
    xero_invoice_number VARCHAR(64) NOT NULL,
    billed_at DATETIME(6) NOT NULL DEFAULT UTC_TIMESTAMP(6),
    minutes_billed INT NOT NULL,
    labour_type_id INT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT UTC_TIMESTAMP(6),
    UNIQUE KEY uq_billed_time_entry (reply_id),
    INDEX idx_billed_time_ticket (ticket_id),
    INDEX idx_billed_time_invoice (xero_invoice_number),
    CONSTRAINT fk_billed_time_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
    CONSTRAINT fk_billed_time_reply FOREIGN KEY (reply_id) REFERENCES ticket_replies(id) ON DELETE CASCADE,
    CONSTRAINT fk_billed_time_labour_type FOREIGN KEY (labour_type_id) REFERENCES ticket_labour_types(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
