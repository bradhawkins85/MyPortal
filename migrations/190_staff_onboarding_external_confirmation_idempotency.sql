-- Migration 190: Idempotency records for staff onboarding external confirmation callbacks

CREATE TABLE IF NOT EXISTS staff_onboarding_external_confirmation_idempotency (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    api_key_id BIGINT UNSIGNED NOT NULL,
    idempotency_key VARCHAR(255) NOT NULL,
    request_fingerprint CHAR(64) NOT NULL,
    company_id BIGINT UNSIGNED NOT NULL,
    staff_id BIGINT UNSIGNED NOT NULL,
    response_status INT NULL,
    response_payload_json LONGTEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_staff_onboarding_external_confirmation_idempotency_key (api_key_id, idempotency_key),
    KEY idx_staff_onboarding_external_confirmation_idempotency_scope (company_id, staff_id),
    CONSTRAINT fk_staff_onboarding_external_confirmation_idempotency_api_key
        FOREIGN KEY (api_key_id) REFERENCES api_keys(id) ON DELETE CASCADE,
    CONSTRAINT fk_staff_onboarding_external_confirmation_idempotency_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_staff_onboarding_external_confirmation_idempotency_staff
        FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE
);
