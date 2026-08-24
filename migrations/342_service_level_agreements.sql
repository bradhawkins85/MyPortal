CREATE TABLE IF NOT EXISTS service_level_agreements (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    company_id INTEGER NOT NULL,
    name VARCHAR(150) NOT NULL,
    response_minutes INTEGER NOT NULL,
    resolution_minutes INTEGER NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_sla_company (company_id),
    CONSTRAINT fk_sla_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ticket_sla_events (
    ticket_id INTEGER NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticket_id, event_type),
    CONSTRAINT fk_ticket_sla_event_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
);

INSERT IGNORE INTO reporting_queries (slug, name, description, sql_query, is_system) VALUES
('dashboard-sla-status', 'Dashboard - Ticket SLA status', 'Current SLA state for every ticket in the selected company.', 'SELECT t.id, t.subject, s.name AS sla, CASE WHEN t.status IN (''closed'',''resolved'') AND TIMESTAMPDIFF(MINUTE,t.created_at,t.closed_at) <= s.resolution_minutes THEN ''met'' WHEN t.status NOT IN (''closed'',''resolved'') AND TIMESTAMPDIFF(MINUTE,t.created_at,CURRENT_TIMESTAMP) > s.resolution_minutes THEN ''breached'' ELSE ''at_risk'' END AS status FROM tickets t JOIN service_level_agreements s ON s.company_id=t.company_id AND s.enabled=1 WHERE t.company_id={{current.company}}', 1),
('dashboard-sla-compliance', 'Dashboard - SLA compliance', 'SLA compliance totals for the selected company.', 'SELECT CASE WHEN t.closed_at IS NOT NULL AND TIMESTAMPDIFF(MINUTE,t.created_at,t.closed_at) <= s.resolution_minutes THEN ''Met'' ELSE ''Breached'' END AS X, COUNT(*) AS Y FROM tickets t JOIN service_level_agreements s ON s.company_id=t.company_id AND s.enabled=1 WHERE t.company_id={{current.company}} AND t.closed_at IS NOT NULL GROUP BY X', 1),
('company-sla-performance', 'Company - SLA performance', 'Ticket response and resolution SLA performance for company reports.', 'SELECT t.id, t.subject, t.created_at, t.closed_at, s.name AS sla, s.response_minutes, s.resolution_minutes, TIMESTAMPDIFF(MINUTE,t.created_at,t.closed_at) AS resolution_minutes_actual FROM tickets t JOIN service_level_agreements s ON s.company_id=t.company_id WHERE t.company_id={{current.company}} ORDER BY t.created_at DESC', 1);
