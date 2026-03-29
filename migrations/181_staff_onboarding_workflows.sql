-- Migration 181: Company-scoped staff onboarding workflow policies and execution tracking

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS company_onboarding_workflow_id VARCHAR(128) NULL AFTER onboarding_fields;

CREATE TABLE IF NOT EXISTS company_onboarding_workflow_policies (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    company_id BIGINT UNSIGNED NOT NULL,
    workflow_key VARCHAR(128) NOT NULL DEFAULT 'staff_onboarding_m365',
    is_enabled TINYINT(1) NOT NULL DEFAULT 1,
    max_retries INT NOT NULL DEFAULT 2,
    config_json LONGTEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_company_onboarding_workflow_policies_company (company_id),
    CONSTRAINT fk_company_onboarding_workflow_policies_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS staff_onboarding_workflow_executions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    company_id BIGINT UNSIGNED NOT NULL,
    staff_id BIGINT UNSIGNED NOT NULL,
    workflow_key VARCHAR(128) NOT NULL DEFAULT 'staff_onboarding_m365',
    state VARCHAR(32) NOT NULL DEFAULT 'requested',
    current_step VARCHAR(128) NULL,
    retries_used INT NOT NULL DEFAULT 0,
    last_error TEXT NULL,
    helpdesk_ticket_id BIGINT UNSIGNED NULL,
    requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at DATETIME NULL,
    completed_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_staff_onboarding_workflow_executions_staff (staff_id),
    KEY idx_staff_onboarding_workflow_executions_company_state (company_id, state),
    CONSTRAINT fk_staff_onboarding_workflow_executions_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_staff_onboarding_workflow_executions_staff
        FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS staff_onboarding_workflow_step_logs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    execution_id BIGINT UNSIGNED NOT NULL,
    step_name VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    attempt INT NOT NULL DEFAULT 1,
    request_payload LONGTEXT NULL,
    response_payload LONGTEXT NULL,
    error_message TEXT NULL,
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_staff_onboarding_workflow_step_logs_execution (execution_id),
    CONSTRAINT fk_staff_onboarding_workflow_step_logs_execution
        FOREIGN KEY (execution_id) REFERENCES staff_onboarding_workflow_executions(id) ON DELETE CASCADE
);
