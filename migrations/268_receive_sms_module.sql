CREATE TABLE IF NOT EXISTS sms_ticket_links (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ticket_id INT NOT NULL,
    from_number VARCHAR(64) NOT NULL,
    from_number_normalized VARCHAR(32) NOT NULL,
    sms_date DATE NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_sms_ticket_links_sender_day (from_number_normalized, sms_date),
    KEY idx_sms_ticket_links_ticket (ticket_id),
    CONSTRAINT fk_sms_ticket_links_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
);
