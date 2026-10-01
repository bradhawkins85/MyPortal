-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

CREATE TABLE IF NOT EXISTS expiration_metadata (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    source_type VARCHAR(32) NOT NULL,
    source_id INT NOT NULL,
    source_field VARCHAR(191) NOT NULL,
    lead_days INT NOT NULL DEFAULT 30,
    owner_user_id INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_expiration_metadata_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_expiration_metadata_owner FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT uq_expiration_source UNIQUE (company_id, source_type, source_id, source_field)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS expiration_reminder_attempts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    source_type VARCHAR(32) NOT NULL,
    source_id INT NOT NULL,
    source_field VARCHAR(191) NOT NULL,
    status VARCHAR(16) NOT NULL,
    error_message VARCHAR(500) NULL,
    attempted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_expiration_attempt_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_expiration_attempt_source ON expiration_reminder_attempts
    (company_id, source_type, source_id, source_field, attempted_at);
