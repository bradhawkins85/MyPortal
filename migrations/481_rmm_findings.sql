-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 481: RMM findings.
--
-- A findings analysis looks across every company's RMM data (event logs,
-- installed software, hardware, stats and checks) for things technicians
-- should know about, first with fixed rules and then with the configured
-- language model (FINDINGS_LLM_*). rmm_finding_runs records each analysis.
--
-- rmm_findings keeps one row per distinct finding (fingerprint, a hash of
-- source and finding_key; the model is shown the keys of current findings so
-- it reuses them for the same issue). A later
-- analysis that finds the same thing updates the row (last_seen_at,
-- occurrences) instead of adding another; one that no longer finds it clears
-- is_active. status is the technician's review: open, acknowledged or
-- dismissed. company_ids_json and asset_ids_json list who it affects.
--
-- Inventory keeps only the latest software list, so rmm_finding_software_snapshots
-- holds what each agent had at the previous analysis and
-- rmm_finding_software_changes the installs, removals and version changes
-- found by comparing the two.

CREATE TABLE IF NOT EXISTS rmm_finding_runs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  status VARCHAR(16) NOT NULL DEFAULT 'running',
  trigger_source VARCHAR(16) NOT NULL DEFAULT 'schedule',
  requested_by_user_id INT NULL,
  started_at DATETIME NOT NULL,
  finished_at DATETIME NULL,
  ai_status VARCHAR(16) NOT NULL DEFAULT 'pending',
  ai_model VARCHAR(255) NULL,
  ai_error VARCHAR(512) NULL,
  error VARCHAR(512) NULL,
  finding_count INT NOT NULL DEFAULT 0,
  stats_json TEXT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_finding_runs_started ON rmm_finding_runs (started_at);

CREATE TABLE IF NOT EXISTS rmm_findings (
  id INT AUTO_INCREMENT PRIMARY KEY,
  fingerprint VARCHAR(64) NOT NULL,
  finding_key VARCHAR(160) NOT NULL,
  source VARCHAR(8) NOT NULL DEFAULT 'rule',
  category VARCHAR(32) NOT NULL,
  severity VARCHAR(16) NOT NULL,
  title VARCHAR(255) NOT NULL,
  summary TEXT NULL,
  recommendation TEXT NULL,
  evidence_json TEXT NULL,
  confidence INT NULL,
  company_ids_json TEXT NULL,
  asset_ids_json TEXT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'open',
  status_note VARCHAR(512) NULL,
  status_changed_at DATETIME NULL,
  status_changed_by_user_id INT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  occurrences INT NOT NULL DEFAULT 1,
  first_seen_at DATETIME NOT NULL,
  last_seen_at DATETIME NOT NULL,
  last_run_id INT NULL,
  CONSTRAINT uq_rmm_findings_fingerprint UNIQUE (fingerprint)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_findings_active ON rmm_findings (is_active, status);
CREATE INDEX IF NOT EXISTS idx_rmm_findings_last_seen ON rmm_findings (last_seen_at);

CREATE TABLE IF NOT EXISTS rmm_finding_software_snapshots (
  agent_id INT NOT NULL PRIMARY KEY,
  items_json LONGTEXT NOT NULL,
  captured_at DATETIME NOT NULL,
  CONSTRAINT fk_rmm_finding_software_snapshots_agent FOREIGN KEY (agent_id) REFERENCES rmm_agents(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rmm_finding_software_changes (
  id INT AUTO_INCREMENT PRIMARY KEY,
  agent_id INT NOT NULL,
  company_id INT NULL,
  asset_id INT NULL,
  name VARCHAR(255) NOT NULL,
  change_type VARCHAR(16) NOT NULL,
  old_version VARCHAR(128) NULL,
  new_version VARCHAR(128) NULL,
  detected_at DATETIME NOT NULL,
  CONSTRAINT fk_rmm_finding_software_changes_agent FOREIGN KEY (agent_id) REFERENCES rmm_agents(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_finding_software_changes_detected ON rmm_finding_software_changes (detected_at);
CREATE INDEX IF NOT EXISTS idx_rmm_finding_software_changes_agent ON rmm_finding_software_changes (agent_id);
