-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

ALTER TABLE websites ADD COLUMN certificate_facts_json TEXT NULL;
ALTER TABLE websites ADD COLUMN certificate_status VARCHAR(32) NULL;
ALTER TABLE websites ADD COLUMN certificate_failure_at DATETIME NULL;
ALTER TABLE websites ADD COLUMN certificate_failure_message VARCHAR(500) NULL;
ALTER TABLE websites ADD COLUMN registration_facts_json TEXT NULL;
ALTER TABLE websites ADD COLUMN registration_status VARCHAR(32) NULL;
ALTER TABLE websites ADD COLUMN registration_failure_at DATETIME NULL;
ALTER TABLE websites ADD COLUMN registration_failure_message VARCHAR(500) NULL;
ALTER TABLE websites ADD COLUMN dns_source VARCHAR(64) NULL;
ALTER TABLE websites ADD COLUMN dns_coverage VARCHAR(32) NULL;
ALTER TABLE websites ADD COLUMN dns_failure_at DATETIME NULL;
ALTER TABLE websites ADD COLUMN dns_failure_message VARCHAR(500) NULL;

CREATE TABLE IF NOT EXISTS website_dns_changes (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    website_id INT NOT NULL,
    observed_at DATETIME NOT NULL,
    source VARCHAR(64) NOT NULL,
    coverage VARCHAR(32) NOT NULL,
    record_name VARCHAR(255) NOT NULL,
    record_type VARCHAR(16) NOT NULL,
    change_kind VARCHAR(16) NOT NULL,
    before_json TEXT NULL,
    after_json TEXT NULL,
    event_hash VARCHAR(64) NOT NULL,
    CONSTRAINT fk_dns_change_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_dns_change_website FOREIGN KEY (website_id) REFERENCES websites(id) ON DELETE CASCADE,
    CONSTRAINT uq_dns_change_event UNIQUE (website_id, event_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_dns_changes_tenant ON website_dns_changes (company_id, website_id, observed_at);
