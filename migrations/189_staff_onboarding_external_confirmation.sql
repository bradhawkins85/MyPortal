-- Migration 189: External confirmation checkpoints for staff onboarding workflows

CREATE TABLE IF NOT EXISTS staff_onboarding_external_checkpoints (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    execution_id BIGINT UNSIGNED NOT NULL,
    company_id BIGINT UNSIGNED NOT NULL,
    staff_id BIGINT UNSIGNED NOT NULL,
    confirmation_token_hash CHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    source VARCHAR(128) NULL,
    callback_timestamp DATETIME NULL,
    proof_reference_id VARCHAR(255) NULL,
    payload_hash VARCHAR(128) NULL,
    callback_payload_json LONGTEXT NULL,
    confirmed_by_api_key_id BIGINT UNSIGNED NULL,
    confirmed_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_staff_onboarding_external_checkpoints_execution (execution_id),
    KEY idx_staff_onboarding_external_checkpoints_scope (company_id, staff_id, status),
    KEY idx_staff_onboarding_external_checkpoints_token (confirmation_token_hash),
    CONSTRAINT fk_staff_onboarding_external_checkpoints_execution
        FOREIGN KEY (execution_id) REFERENCES staff_onboarding_workflow_executions(id) ON DELETE CASCADE,
    CONSTRAINT fk_staff_onboarding_external_checkpoints_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_staff_onboarding_external_checkpoints_staff
        FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE,
    CONSTRAINT fk_staff_onboarding_external_checkpoints_api_key
        FOREIGN KEY (confirmed_by_api_key_id) REFERENCES api_keys(id) ON DELETE SET NULL
);
