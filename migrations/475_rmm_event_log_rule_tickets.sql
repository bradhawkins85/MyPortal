-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 475: tickets raised by event log rules.
--
-- An event log rule with action 'create_ticket' raises a ticket for the
-- device's company with the asset linked. While that ticket is not closed,
-- later matches on the same device add an update to it instead of raising
-- another, so this remembers the ticket per rule and device (agent).

CREATE TABLE IF NOT EXISTS rmm_event_log_rule_tickets (
  rule_id INT NOT NULL,
  agent_id INT NOT NULL,
  ticket_id INT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (rule_id, agent_id),
  CONSTRAINT fk_rmm_event_log_rule_tickets_rule FOREIGN KEY (rule_id) REFERENCES rmm_event_log_rules(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
