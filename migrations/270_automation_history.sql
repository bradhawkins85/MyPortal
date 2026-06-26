-- Automation action history
-- Stores one row per action/ticket change performed by automations.

CREATE TABLE IF NOT EXISTS automation_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    automation_id INT NOT NULL,
    automation_run_id INT NULL,
    occurred_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    action_name VARCHAR(255) NOT NULL,
    action_module VARCHAR(64) NULL,
    ticket_id INT NULL,
    ticket_number VARCHAR(64) NULL,
    status VARCHAR(32) NOT NULL,
    previous_values JSON NULL,
    result_payload JSON NULL,
    error_message TEXT NULL,
    CONSTRAINT fk_automation_history_automation FOREIGN KEY (automation_id) REFERENCES automations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_automation_history_automation_time ON automation_history (automation_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_automation_history_ticket ON automation_history (ticket_id);
CREATE INDEX IF NOT EXISTS idx_automation_history_status ON automation_history (status);
