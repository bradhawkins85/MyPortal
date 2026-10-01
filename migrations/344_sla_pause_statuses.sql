CREATE TABLE IF NOT EXISTS sla_template_pause_statuses (
    template_id INTEGER NOT NULL,
    status VARCHAR(64) NOT NULL,
    PRIMARY KEY (template_id, status),
    CONSTRAINT fk_sla_pause_template FOREIGN KEY (template_id) REFERENCES sla_templates(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ticket_status_history (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    ticket_id INTEGER NOT NULL,
    status VARCHAR(64) NOT NULL,
    started_at DATETIME NOT NULL,
    ended_at DATETIME NOT NULL,
    CONSTRAINT fk_ticket_status_history_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
);

CREATE INDEX idx_ticket_status_history_ticket ON ticket_status_history (ticket_id, started_at);

INSERT IGNORE INTO sla_template_pause_statuses (template_id, status)
SELECT id, statuses.status FROM sla_templates
CROSS JOIN (
    SELECT 'resolved' AS status UNION ALL SELECT 'closed'
) statuses;
