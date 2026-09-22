-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- DEPLOYMENT: Green/Blue safe; this adds isolated analytics tables only.

CREATE TABLE IF NOT EXISTS ai_quality_responses (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    company_id BIGINT NULL,
    feature VARCHAR(40) NOT NULL,
    query_hash CHAR(64) NOT NULL,
    query_redacted TEXT NOT NULL,
    evidence_identifiers JSON NOT NULL,
    source_types JSON NOT NULL,
    primary_source_type VARCHAR(40) NULL,
    provider VARCHAR(80) NULL,
    model VARCHAR(160) NULL,
    pipeline_version VARCHAR(80) NOT NULL,
    latency_ms INTEGER NOT NULL,
    confidence_band VARCHAR(20) NULL,
    outcome VARCHAR(30) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_ai_quality_response_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ai_quality_feedback (
    id INT AUTO_INCREMENT PRIMARY KEY,
    response_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    rating VARCHAR(4) NOT NULL,
    reason VARCHAR(40) NULL,
    comment VARCHAR(1000) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE (response_id, user_id),
    CONSTRAINT fk_ai_quality_feedback_response FOREIGN KEY (response_id) REFERENCES ai_quality_responses(id) ON DELETE CASCADE,
    CONSTRAINT fk_ai_quality_feedback_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_ai_quality_response_scope ON ai_quality_responses (company_id, feature, created_at);
