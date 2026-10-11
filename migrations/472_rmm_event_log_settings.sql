-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 472: which extra Windows event logs RMM agents collect.
--
-- Every agent sends its Application and System logs. A row here adds more
-- logs (such as Security or Microsoft-Windows-PowerShell/Operational) for a
-- whole company (scope_key 'company:<id>') or one device ('asset:<id>').
-- extra_logs is a JSON list of log names. The entries themselves live in the
-- separate event log database (EVENT_LOG_DB_*), not in this one.

CREATE TABLE IF NOT EXISTS rmm_event_log_settings (
  scope_key VARCHAR(32) NOT NULL PRIMARY KEY,
  company_id INT NULL,
  asset_id INT NULL,
  extra_logs TEXT NOT NULL,
  updated_by_user_id INT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_rmm_event_log_settings_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_event_log_settings_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
