ALTER TABLE company_essential8_requirement_compliance
    ADD COLUMN IF NOT EXISTS target_compliance_date DATE NULL;

ALTER TABLE company_essential8_requirement_compliance
    ADD COLUMN IF NOT EXISTS owner_user_id INT NULL;

ALTER TABLE company_essential8_requirement_compliance
    ADD COLUMN IF NOT EXISTS approval_status VARCHAR(32) NOT NULL DEFAULT 'draft';

ALTER TABLE company_essential8_requirement_compliance
    ADD COLUMN IF NOT EXISTS approval_notes TEXT NULL;

ALTER TABLE company_essential8_requirement_compliance
    ADD COLUMN IF NOT EXISTS approved_by INT NULL;

ALTER TABLE company_essential8_requirement_compliance
    ADD COLUMN IF NOT EXISTS approved_at DATETIME NULL;

ALTER TABLE company_essential8_requirement_compliance
    ADD COLUMN IF NOT EXISTS reminder_days_before INT NULL DEFAULT 14;

ALTER TABLE company_essential8_requirement_compliance
    ADD COLUMN IF NOT EXISTS overdue_alert_enabled TINYINT(1) NOT NULL DEFAULT 1;

ALTER TABLE company_essential8_requirement_compliance
    ADD COLUMN IF NOT EXISTS last_reminder_sent_at DATETIME NULL;

ALTER TABLE company_essential8_requirement_compliance
    ADD COLUMN IF NOT EXISTS last_overdue_alert_at DATETIME NULL;

CREATE TABLE IF NOT EXISTS company_essential8_requirement_evidence (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    requirement_id INT NOT NULL,
    version_number INT NOT NULL DEFAULT 1,
    title VARCHAR(255) NOT NULL,
    description TEXT NULL,
    file_name VARCHAR(255) NOT NULL,
    content_type VARCHAR(255) NULL,
    file_path VARCHAR(500) NOT NULL,
    file_size_bytes BIGINT NULL,
    uploaded_by INT NULL,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_current TINYINT(1) NOT NULL DEFAULT 1,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    FOREIGN KEY (requirement_id) REFERENCES essential8_requirements(id) ON DELETE CASCADE,
    FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE KEY unique_requirement_evidence_version (company_id, requirement_id, version_number),
    INDEX idx_requirement_evidence_lookup (company_id, requirement_id, is_current)
);

CREATE TABLE IF NOT EXISTS company_essential8_requirement_audit (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    requirement_id INT NOT NULL,
    user_id INT NULL,
    action VARCHAR(64) NOT NULL,
    from_status VARCHAR(32) NULL,
    to_status VARCHAR(32) NULL,
    approval_status VARCHAR(32) NULL,
    change_summary TEXT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    FOREIGN KEY (requirement_id) REFERENCES essential8_requirements(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    INDEX idx_requirement_audit_lookup (company_id, requirement_id, created_at)
);
