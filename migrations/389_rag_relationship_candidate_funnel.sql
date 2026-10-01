-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

CREATE TABLE IF NOT EXISTS rag_relationship_candidate_runs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    document_id INT NOT NULL,
    eligible_documents INT NOT NULL DEFAULT 0,
    prefiltered_pairs INT NOT NULL DEFAULT 0,
    queued_evaluations INT NOT NULL DEFAULT 0,
    positive_matches INT NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_rag_candidate_run_document
        FOREIGN KEY (document_id) REFERENCES rag_documents(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rag_candidate_runs_document_created
    ON rag_relationship_candidate_runs (document_id, created_at);
