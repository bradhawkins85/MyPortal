-- Voice Monitor product contract: one subscription unit grants one enabled
-- monitored number. Attempts, connected minutes and completed transcriptions
-- are immutable billing facts. A logical attempt can therefore only be billed
-- once even when workers retry or providers repeat callbacks.
CREATE TABLE voice_monitor_usage_ledger (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    attempt_id BIGINT UNSIGNED NOT NULL,
    subscription_id VARCHAR(36) NOT NULL,
    company_id INT NOT NULL,
    occurred_at DATETIME(6) NOT NULL,
    attempt_units SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    connected_minutes INT UNSIGNED NOT NULL DEFAULT 0,
    transcription_units SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    attempt_price DECIMAL(10,4) NOT NULL,
    minute_price DECIMAL(10,4) NOT NULL,
    transcription_price DECIMAL(10,4) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT uq_vm_usage_attempt UNIQUE (attempt_id),
    CONSTRAINT fk_vm_usage_attempt FOREIGN KEY (attempt_id) REFERENCES voice_monitor_attempts(id) ON DELETE RESTRICT,
    CONSTRAINT fk_vm_usage_subscription FOREIGN KEY (subscription_id) REFERENCES subscriptions(id) ON DELETE RESTRICT,
    CONSTRAINT fk_vm_usage_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE RESTRICT,
    INDEX idx_vm_usage_invoice (subscription_id, occurred_at)
);

-- The ledger is append-only. Database privileges should grant INSERT/SELECT but
-- no UPDATE/DELETE to the application account; these triggers provide defence
-- in depth for installations whose application account owns the schema.
CREATE TRIGGER voice_monitor_usage_no_update BEFORE UPDATE ON voice_monitor_usage_ledger
FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Voice Monitor usage ledger is immutable';
CREATE TRIGGER voice_monitor_usage_no_delete BEFORE DELETE ON voice_monitor_usage_ledger
FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Voice Monitor usage ledger is immutable';
