-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Record any AI output (what it is about, what it links to, the text) so every
-- AI feature can be rated 👍/👎, and snapshot whether each voter was a
-- technician: only technician votes teach. These replace ai_quality_responses
-- and ai_quality_feedback (migration 387), which never received rows because
-- their insert path was broken; a later contract migration drops them.
-- * Collation matches rag_documents so the Related-item backfill can compare.
-- * user_id is SET NULL so deleting one user never deletes others' votes.
-- * Empty strings (not NULL) for subject/target keep the identity UNIQUE and
--   indexable, so concurrent votes cannot create duplicate outputs.
CREATE TABLE IF NOT EXISTS ai_outputs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NULL,
    company_id INT NULL,
    feature VARCHAR(40) NOT NULL,
    subject_type VARCHAR(40) NOT NULL DEFAULT '',
    subject_id VARCHAR(64) NOT NULL DEFAULT '',
    target_type VARCHAR(40) NOT NULL DEFAULT '',
    target_id VARCHAR(64) NOT NULL DEFAULT '',
    output_hash CHAR(64) NOT NULL,
    output_text MEDIUMTEXT NULL,
    query_hash CHAR(64) NULL,
    query_redacted TEXT NULL,
    evidence_identifiers JSON NULL,
    source_types JSON NULL,
    primary_source_type VARCHAR(40) NULL,
    provider VARCHAR(80) NULL,
    model VARCHAR(160) NULL,
    pipeline_version VARCHAR(80) NOT NULL,
    prompt_version VARCHAR(40) NULL,
    latency_ms INT NOT NULL DEFAULT 0,
    confidence_band VARCHAR(20) NULL,
    outcome VARCHAR(30) NOT NULL DEFAULT 'answered',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_ai_outputs_identity UNIQUE (feature, subject_type, subject_id, target_type, target_id, output_hash),
    CONSTRAINT fk_ai_outputs_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_ai_outputs_scope ON ai_outputs (company_id, feature, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_outputs_target ON ai_outputs (target_type, target_id);

CREATE TABLE IF NOT EXISTS ai_output_feedback (
    id INT AUTO_INCREMENT PRIMARY KEY,
    response_id INT NOT NULL,
    user_id INT NOT NULL,
    rating VARCHAR(4) NOT NULL,
    reason VARCHAR(40) NULL,
    comment VARCHAR(1000) NULL,
    is_technician TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT uq_ai_output_feedback_vote UNIQUE (response_id, user_id),
    CONSTRAINT fk_ai_output_feedback_output FOREIGN KEY (response_id) REFERENCES ai_outputs(id) ON DELETE CASCADE,
    CONSTRAINT fk_ai_output_feedback_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_ai_output_feedback_updated ON ai_output_feedback (updated_at);
