ALTER TABLE tickets
    ADD COLUMN ticket_number VARCHAR(64) NULL AFTER external_reference;

ALTER TABLE ticket_replies
    ADD COLUMN external_reference VARCHAR(128) NULL AFTER body,
    ADD UNIQUE KEY uq_ticket_replies_external (ticket_id, external_reference);
