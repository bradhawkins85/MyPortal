-- Durable delivery metadata. Provider secrets belong in environment/module
-- settings; only opaque provider identifiers are stored here.
ALTER TABLE voice_monitor_attempts
    MODIFY outcome_status ENUM('queued','retry_wait','dialing','answered','interrupted','passed','failed','timed_out','cancelled','exhausted') NOT NULL DEFAULT 'queued',
    ADD COLUMN scheduled_for DATETIME(6) NULL AFTER queued_at,
    ADD COLUMN available_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) AFTER scheduled_for,
    ADD COLUMN dispatch_key CHAR(64) NULL AFTER available_at,
    ADD COLUMN provider_idempotency_key CHAR(64) NULL AFTER dispatch_key,
    ADD COLUMN delivery_count SMALLINT UNSIGNED NOT NULL DEFAULT 0 AFTER retry_count,
    ADD COLUMN max_deliveries SMALLINT UNSIGNED NOT NULL DEFAULT 1 AFTER delivery_count,
    ADD COLUMN lease_owner VARCHAR(255) NULL AFTER worker_identity,
    ADD COLUMN lease_until DATETIME(6) NULL AFTER lease_owner,
    ADD COLUMN heartbeat_at DATETIME(6) NULL AFTER lease_until,
    ADD UNIQUE INDEX uq_voice_monitor_dispatch (dispatch_key),
    ADD UNIQUE INDEX uq_voice_monitor_provider_idempotency (provider_idempotency_key),
    ADD INDEX idx_voice_monitor_claim (outcome_status, available_at, lease_until, id),
    ADD INDEX idx_voice_monitor_tenant_claim (company_id, outcome_status, available_at);
