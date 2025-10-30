CREATE TABLE IF NOT EXISTS ticket_statuses (
    id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    tech_status VARCHAR(64) NOT NULL,
    tech_label VARCHAR(128) NOT NULL,
    public_status VARCHAR(128) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT UTC_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT UTC_TIMESTAMP(6) ON UPDATE UTC_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_ticket_statuses_status (tech_status)
);

INSERT INTO ticket_statuses (tech_status, tech_label, public_status)
VALUES
    ('open', 'Open', 'Open'),
    ('in_progress', 'In progress', 'In progress'),
    ('pending', 'Pending', 'Pending'),
    ('resolved', 'Resolved', 'Resolved'),
    ('closed', 'Closed', 'Closed')
ON DUPLICATE KEY UPDATE
    tech_label = VALUES(tech_label),
    public_status = VALUES(public_status),
    updated_at = UTC_TIMESTAMP(6);
