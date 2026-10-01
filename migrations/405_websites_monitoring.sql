-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

CREATE TABLE IF NOT EXISTS websites (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    name VARCHAR(191) NOT NULL,
    url VARCHAR(2048) NOT NULL,
    owner VARCHAR(255) NULL,
    notes TEXT NULL,
    monitor_availability BOOLEAN NOT NULL DEFAULT TRUE,
    monitor_tls BOOLEAN NOT NULL DEFAULT TRUE,
    collect_dns BOOLEAN NOT NULL DEFAULT FALSE,
    collect_domain_expiry BOOLEAN NOT NULL DEFAULT FALSE,
    last_success_at DATETIME NULL,
    last_failure_at DATETIME NULL,
    last_failure_message VARCHAR(500) NULL,
    last_http_status INT NULL,
    certificate_expires_at DATETIME NULL,
    certificate_expiry_source VARCHAR(64) NULL,
    certificate_checked_at DATETIME NULL,
    domain_expires_at DATETIME NULL,
    domain_expiry_source VARCHAR(64) NULL,
    domain_checked_at DATETIME NULL,
    dns_facts_json TEXT NULL,
    dns_checked_at DATETIME NULL,
    created_by INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_website_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_website_creator FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS website_asset_links (
    website_id INT NOT NULL, asset_id INT NOT NULL,
    PRIMARY KEY (website_id, asset_id),
    CONSTRAINT fk_website_asset_website FOREIGN KEY (website_id) REFERENCES websites(id) ON DELETE CASCADE,
    CONSTRAINT fk_website_asset_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS website_kb_links (
    website_id INT NOT NULL, article_id INT NOT NULL,
    PRIMARY KEY (website_id, article_id),
    CONSTRAINT fk_website_kb_website FOREIGN KEY (website_id) REFERENCES websites(id) ON DELETE CASCADE,
    CONSTRAINT fk_website_kb_article FOREIGN KEY (article_id) REFERENCES knowledge_base_articles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS website_check_jobs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    website_id INT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    attempt_count INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 3,
    available_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_expires_at DATETIME NULL,
    last_error VARCHAR(500) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME NULL,
    CONSTRAINT fk_website_job_website FOREIGN KEY (website_id) REFERENCES websites(id) ON DELETE CASCADE
);

CREATE INDEX idx_websites_company ON websites (company_id, name);
CREATE INDEX idx_website_jobs_ready ON website_check_jobs (status, available_at, lease_expires_at);
