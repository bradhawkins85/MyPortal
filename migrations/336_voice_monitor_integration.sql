-- Provider secrets are ciphertext produced by the application key manager; no
-- plaintext credential column is provided.
INSERT INTO integration_modules (slug, name, description, icon, enabled, settings)
VALUES ('voice-monitor', 'Voice Monitor', 'Bounded health-check calls to subscribed numbers.', '📞', 0,
        '{"provider_type":"disabled","endpoint":"","credentials_encrypted":"","caller_identity":"","per_user_hourly_limit":3,"per_company_hourly_limit":10,"recording_retention_days":30,"worker_concurrency":5,"worker_lease_seconds":300,"test_calls_enabled":false}')
ON DUPLICATE KEY UPDATE name = VALUES(name), description = VALUES(description), icon = VALUES(icon);

CREATE TABLE voice_monitor_preferences (
    company_id INT NOT NULL PRIMARY KEY,
    allow_test_calls TINYINT(1) NOT NULL DEFAULT 0,
    recording_enabled TINYINT(1) NOT NULL DEFAULT 0,
    notify_on_failure TINYINT(1) NOT NULL DEFAULT 1,
    updated_by INT NULL, updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_vm_pref_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_vm_pref_actor FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
);

ALTER TABLE voice_monitor_attempts
    ADD COLUMN initiated_by_user_id INT NULL AFTER company_id,
    ADD COLUMN request_idempotency_token VARCHAR(128) NULL AFTER initiated_by_user_id,
    ADD CONSTRAINT fk_vm_attempt_actor FOREIGN KEY (initiated_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
    ADD UNIQUE INDEX uq_vm_manual_idempotency (company_id, initiated_by_user_id, request_idempotency_token),
    ADD INDEX idx_vm_manual_limits (company_id, initiated_by_user_id, queued_at);
