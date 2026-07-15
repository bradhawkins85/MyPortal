CREATE TABLE IF NOT EXISTS ticket_shipment_watches (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ticket_id INT NOT NULL,
    tracking_url VARCHAR(500) NOT NULL,
    provider VARCHAR(64) NOT NULL,
    consignment_id VARCHAR(128) NULL,
    poll_interval_seconds INT NOT NULL DEFAULT 900,
    last_snapshot_hash CHAR(64) NULL,
    last_snapshot_json LONGTEXT NULL,
    last_checked_at DATETIME(6) NULL,
    last_posted_update_at DATETIME(6) NULL,
    active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_ticket_shipment_watches_ticket (ticket_id),
    INDEX idx_ticket_shipment_watches_active_checked (active, last_checked_at),
    INDEX idx_ticket_shipment_watches_provider_active (provider, active),
    CONSTRAINT fk_ticket_shipment_watches_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
