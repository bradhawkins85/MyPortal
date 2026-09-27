-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

ALTER TABLE website_check_jobs
    ADD COLUMN check_type VARCHAR(20) NOT NULL DEFAULT 'website';

CREATE INDEX idx_website_jobs_type_ready
    ON website_check_jobs (check_type, status, available_at);
