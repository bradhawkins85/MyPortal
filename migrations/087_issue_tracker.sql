CREATE TABLE IF NOT EXISTS issue_definitions (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT NULL,
    created_by BIGINT UNSIGNED NULL,
    updated_by BIGINT UNSIGNED NULL,
    created_at_utc DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at_utc DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    UNIQUE KEY uniq_issue_definitions_name (name),
    KEY idx_issue_definitions_created_at (created_at_utc),
    CONSTRAINT fk_issue_definitions_created_by FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT fk_issue_definitions_updated_by FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS issue_company_statuses (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    issue_id BIGINT UNSIGNED NOT NULL,
    company_id BIGINT UNSIGNED NOT NULL,
    status VARCHAR(32) NOT NULL,
    notes TEXT NULL,
    updated_by BIGINT UNSIGNED NULL,
    created_at_utc DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at_utc DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    UNIQUE KEY uniq_issue_company (issue_id, company_id),
    KEY idx_issue_company_status (status),
    KEY idx_issue_company_updated_at (updated_at_utc),
    CONSTRAINT fk_issue_company_issue FOREIGN KEY (issue_id) REFERENCES issue_definitions(id) ON DELETE CASCADE,
    CONSTRAINT fk_issue_company_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_issue_company_updated_by FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
);
