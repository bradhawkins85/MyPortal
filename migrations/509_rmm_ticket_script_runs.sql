-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 509: RMM scripts run from ticket automations.
--
-- The "My Portal RMM Script" ticket automation action queues a Gitea script
-- on the ticket's linked devices as ordinary RMM runs (run_source 'ticket').
-- Each run is recorded here against its ticket so the result can be posted
-- back as an internal note. batch_id groups the runs one action queued, and
-- success_status is the ticket status to set once all of them complete
-- successfully (NULL leaves the status alone). reported_at is set once the
-- outcome has been posted, including for runs that expire or are cancelled.

CREATE TABLE IF NOT EXISTS rmm_ticket_script_runs (
  run_id INT NOT NULL PRIMARY KEY,
  ticket_id INT NOT NULL,
  batch_id VARCHAR(36) NOT NULL,
  success_status VARCHAR(64) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reported_at DATETIME NULL,
  CONSTRAINT fk_rmm_ticket_script_runs_run FOREIGN KEY (run_id) REFERENCES rmm_script_runs(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_ticket_script_runs_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_ticket_script_runs_ticket ON rmm_ticket_script_runs (ticket_id);
CREATE INDEX IF NOT EXISTS idx_rmm_ticket_script_runs_batch ON rmm_ticket_script_runs (batch_id);
CREATE INDEX IF NOT EXISTS idx_rmm_ticket_script_runs_reported ON rmm_ticket_script_runs (reported_at);
