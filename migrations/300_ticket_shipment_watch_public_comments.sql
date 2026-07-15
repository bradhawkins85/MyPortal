ALTER TABLE ticket_shipment_watches
    ADD COLUMN public_comments_enabled TINYINT(1) NOT NULL DEFAULT 1 AFTER active;
