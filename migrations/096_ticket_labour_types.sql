CREATE TABLE ticket_labour_types (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    code VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT UTC_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT UTC_TIMESTAMP(6),
    UNIQUE KEY uq_ticket_labour_code (code)
);

ALTER TABLE ticket_replies
    ADD COLUMN labour_type_id INT NULL AFTER minutes_spent;

ALTER TABLE ticket_replies
    ADD CONSTRAINT fk_ticket_replies_labour_type
    FOREIGN KEY (labour_type_id) REFERENCES ticket_labour_types(id)
    ON DELETE SET NULL;

CREATE INDEX idx_ticket_replies_labour_type
    ON ticket_replies(labour_type_id);
