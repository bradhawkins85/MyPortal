-- Explicit customer authority and conservative per-destination controls.
ALTER TABLE voice_monitor_endpoints
 ADD COLUMN consent_granted TINYINT(1) NOT NULL DEFAULT 0,
 ADD COLUMN recording_consent_granted TINYINT(1) NOT NULL DEFAULT 0,
 ADD COLUMN consent_actor_id INT NULL,
 ADD COLUMN consent_at DATETIME(6) NULL,
 ADD COLUMN consent_policy_version VARCHAR(64) NULL,
 ADD COLUMN consent_revoked_at DATETIME(6) NULL,
 ADD COLUMN quiet_hours_start TIME NOT NULL DEFAULT '20:00:00',
 ADD COLUMN quiet_hours_end TIME NOT NULL DEFAULT '08:00:00',
 ADD COLUMN caller_id_verified TINYINT(1) NOT NULL DEFAULT 0,
 ADD COLUMN daily_attempt_limit INT UNSIGNED NOT NULL DEFAULT 10,
 ADD COLUMN monetary_cap_minor BIGINT UNSIGNED NOT NULL DEFAULT 0,
 ADD CONSTRAINT fk_vm_consent_actor FOREIGN KEY (consent_actor_id) REFERENCES users(id) ON DELETE SET NULL,
 ADD INDEX idx_vm_consent (enabled,consent_granted,consent_revoked_at,next_run_at);

ALTER TABLE voice_monitor_attempts ADD COLUMN final_callback_at DATETIME(6) NULL;

CREATE TABLE voice_monitor_callback_events (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
 provider_event_id VARCHAR(255) NOT NULL,
 event_hash CHAR(64) NOT NULL,
 provider_call_id VARCHAR(255) NOT NULL,
 event_status VARCHAR(32) NOT NULL,
 received_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 UNIQUE INDEX uq_vm_callback_event (provider_event_id),
 UNIQUE INDEX uq_vm_callback_replay (event_hash),
 INDEX idx_vm_callback_call (provider_call_id,received_at)
);

CREATE TABLE voice_monitor_worker_heartbeats (
 worker_identity VARCHAR(255) NOT NULL PRIMARY KEY,
 heartbeat_at DATETIME(6) NOT NULL,
 active_calls INT UNSIGNED NOT NULL DEFAULT 0,
 queue_depth INT UNSIGNED NOT NULL DEFAULT 0,
 provider_latency_ms INT UNSIGNED NULL,
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 INDEX idx_vm_worker_health (heartbeat_at)
);
