-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

ALTER TABLE website_check_jobs ADD COLUMN lease_owner VARCHAR(64) NULL;
ALTER TABLE website_check_jobs ADD COLUMN started_at DATETIME NULL;
ALTER TABLE website_check_jobs ADD COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE website_check_jobs ADD COLUMN idempotency_key VARCHAR(191) NULL;
ALTER TABLE websites ADD COLUMN next_check_at DATETIME NULL;

CREATE INDEX idx_websites_next_check ON websites (next_check_at, company_id);
CREATE UNIQUE INDEX uq_website_job_idempotency ON website_check_jobs (idempotency_key);
