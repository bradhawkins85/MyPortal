-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Durable, tenant-scoped execution records for long-running M365 operations.

CREATE TABLE IF NOT EXISTS m365_jobs (
  id VARCHAR(36) PRIMARY KEY,
  company_id BIGINT NOT NULL,
  job_type VARCHAR(64) NOT NULL,
  resource_key VARCHAR(255) NOT NULL,
  active_key VARCHAR(255) NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'queued',
  payload TEXT NULL,
  result TEXT NULL,
  safe_error VARCHAR(1000) NULL,
  attempt_count INT NOT NULL DEFAULT 0,
  owner_id VARCHAR(255) NULL,
  lease_expires_at DATETIME NULL,
  heartbeat_at DATETIME NULL,
  available_at DATETIME NOT NULL,
  created_at DATETIME NOT NULL,
  started_at DATETIME NULL,
  completed_at DATETIME NULL,
  updated_at DATETIME NOT NULL,
  UNIQUE (company_id, active_key)
);

CREATE INDEX IF NOT EXISTS idx_m365_jobs_claim
  ON m365_jobs (status, available_at, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_m365_jobs_tenant
  ON m365_jobs (company_id, created_at);
