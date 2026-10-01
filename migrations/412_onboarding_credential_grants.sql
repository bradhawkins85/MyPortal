-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

CREATE TABLE IF NOT EXISTS credential_grants (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    credential_id BIGINT NOT NULL,
    credential_version INT NOT NULL,
    company_id INT NOT NULL,
    staff_id INT NOT NULL,
    grantor_user_id INT NOT NULL,
    recipient_user_id INT NULL,
    recipient_email VARCHAR(254) NULL,
    reason VARCHAR(500) NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    token_hash BINARY(32) NULL,
    verification_hash BINARY(32) NULL,
    verified_at DATETIME(6) NULL,
    opened_at DATETIME(6) NULL,
    revealed_at DATETIME(6) NULL,
    consumed_at DATETIME(6) NULL,
    revoked_at DATETIME(6) NULL,
    expired_audited_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_credential_grant_credential FOREIGN KEY (credential_id) REFERENCES credentials(id) ON DELETE CASCADE,
    CONSTRAINT fk_credential_grant_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_credential_grant_staff FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE,
    CONSTRAINT fk_credential_grant_grantor FOREIGN KEY (grantor_user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_credential_grant_recipient FOREIGN KEY (recipient_user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT chk_credential_grant_recipient CHECK ((recipient_user_id IS NOT NULL AND recipient_email IS NULL) OR (recipient_user_id IS NULL AND recipient_email IS NOT NULL)),
    UNIQUE KEY uq_credential_grant_token (token_hash),
    INDEX idx_credential_grant_recipient (company_id, recipient_user_id, expires_at),
    INDEX idx_credential_grant_credential (company_id, credential_id, credential_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
