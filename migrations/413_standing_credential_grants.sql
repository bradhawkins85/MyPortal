-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

ALTER TABLE staff
    ADD COLUMN IF NOT EXISTS portal_user_id INT NULL;

CREATE INDEX IF NOT EXISTS idx_staff_portal_user
    ON staff (company_id, portal_user_id, enabled);

CREATE TABLE IF NOT EXISTS credential_standing_grants (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    credential_id BIGINT NOT NULL,
    company_id INT NOT NULL,
    selector_type VARCHAR(16) NOT NULL,
    staff_id INT NULL,
    job_title VARCHAR(255) NULL,
    job_title_key VARCHAR(255) NULL,
    can_enumerate TINYINT(1) NOT NULL DEFAULT 0,
    can_reveal TINYINT(1) NOT NULL DEFAULT 0,
    can_share TINYINT(1) NOT NULL DEFAULT 0,
    can_administer TINYINT(1) NOT NULL DEFAULT 0,
    purpose VARCHAR(500) NOT NULL,
    grantor_user_id INT NOT NULL,
    approver_user_id INT NULL,
    expires_at DATETIME(6) NULL,
    review_due_at DATETIME(6) NOT NULL,
    reviewed_at DATETIME(6) NULL,
    revoked_at DATETIME(6) NULL,
    revoked_by_user_id INT NULL,
    last_resolved_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_standing_grant_credential FOREIGN KEY (credential_id) REFERENCES credentials(id) ON DELETE CASCADE,
    CONSTRAINT fk_standing_grant_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_standing_grant_staff FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE,
    CONSTRAINT fk_standing_grant_grantor FOREIGN KEY (grantor_user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_standing_grant_approver FOREIGN KEY (approver_user_id) REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT fk_standing_grant_revoker FOREIGN KEY (revoked_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT chk_standing_grant_selector CHECK (
        (selector_type = 'staff' AND staff_id IS NOT NULL AND job_title_key IS NULL)
        OR (selector_type = 'job_title' AND staff_id IS NULL AND job_title_key IS NOT NULL)
    ),
    CONSTRAINT chk_standing_grant_capability CHECK (
        can_enumerate = 1 OR can_reveal = 1 OR can_share = 1 OR can_administer = 1
    ),
    INDEX idx_standing_grant_item (company_id, credential_id, revoked_at),
    INDEX idx_standing_grant_staff (company_id, staff_id, revoked_at),
    INDEX idx_standing_grant_title (company_id, job_title_key, revoked_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
