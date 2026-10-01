-- Content has a shorter, configurable lifecycle than operational attempt
-- evidence.  Application retrieval must always include company_id.
CREATE TABLE voice_monitor_contents (
    attempt_id BIGINT UNSIGNED NOT NULL,
    company_id INT NOT NULL,
    media_reference VARCHAR(255) NULL COMMENT 'Opaque identifier; never a public path',
    transcript_reference VARCHAR(255) NULL COMMENT 'Opaque identifier',
    transcript_text MEDIUMTEXT NULL,
    transcript_status ENUM('not_requested','pending','processing','completed','failed') NOT NULL DEFAULT 'not_requested',
    transcription_failure_code VARCHAR(64) NULL,
    retain_until DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (attempt_id),
    CONSTRAINT fk_voice_monitor_content_attempt FOREIGN KEY (attempt_id) REFERENCES voice_monitor_attempts(id) ON DELETE CASCADE,
    CONSTRAINT fk_voice_monitor_content_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE RESTRICT,
    INDEX idx_voice_monitor_content_retention (retain_until),
    INDEX idx_voice_monitor_content_tenant (company_id, attempt_id)
);

-- Migrate references out of operational evidence. Existing columns remain for
-- rolling-deployment compatibility and can be removed after all nodes upgrade.
INSERT INTO voice_monitor_contents
    (attempt_id, company_id, media_reference, transcript_reference, transcript_status)
SELECT id, company_id, media_artifact_reference, transcript_text_reference, transcript_status
FROM voice_monitor_attempts;
