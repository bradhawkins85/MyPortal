CREATE TABLE IF NOT EXISTS m365_spam_purge_requests (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    created_by INT NOT NULL,
    search_name VARCHAR(180) NOT NULL,
    action_name VARCHAR(190) NOT NULL,
    content_match_query TEXT NOT NULL,
    sender VARCHAR(320) NULL,
    subject VARCHAR(500) NULL,
    received_from DATE NULL,
    received_to DATE NULL,
    search_status VARCHAR(32) NOT NULL DEFAULT 'draft',
    purge_status VARCHAR(32) NOT NULL DEFAULT 'not_started',
    matched_items INT NOT NULL DEFAULT 0,
    matched_size BIGINT NOT NULL DEFAULT 0,
    removed_items INT NOT NULL DEFAULT 0,
    search_details TEXT NULL,
    purge_details TEXT NULL,
    error_message TEXT NULL,
    search_started_at TIMESTAMP NULL,
    search_completed_at TIMESTAMP NULL,
    purge_started_at TIMESTAMP NULL,
    purge_completed_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_m365_spam_purge_company FOREIGN KEY (company_id) REFERENCES companies (id) ON DELETE CASCADE,
    CONSTRAINT fk_m365_spam_purge_user FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT uq_m365_spam_purge_search_name UNIQUE (search_name)
);

CREATE INDEX idx_m365_spam_purge_company_created
    ON m365_spam_purge_requests (company_id, created_at);

CREATE INDEX idx_m365_spam_purge_status
    ON m365_spam_purge_requests (search_status, purge_status);
