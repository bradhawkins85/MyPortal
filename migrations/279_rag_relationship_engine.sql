CREATE TABLE IF NOT EXISTS rag_relationships (
    id INT AUTO_INCREMENT PRIMARY KEY,
    source_document_id INT NOT NULL,
    target_document_id INT NOT NULL,
    relationship_type VARCHAR(64) NOT NULL DEFAULT 'NOT_RELEVANT',
    match_status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    relevance_score DECIMAL(5,4) NOT NULL DEFAULT 0,
    confidence DECIMAL(5,4) NOT NULL DEFAULT 0,
    reason TEXT NULL,
    supporting_excerpt TEXT NULL,
    evaluated_model VARCHAR(128) NULL,
    evaluated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    source_hash VARCHAR(64) NOT NULL,
    target_hash VARCHAR(64) NOT NULL,
    evaluation_duration_ms INT NOT NULL DEFAULT 0,
    CONSTRAINT fk_rag_relationship_source FOREIGN KEY (source_document_id) REFERENCES rag_documents(id) ON DELETE CASCADE,
    CONSTRAINT fk_rag_relationship_target FOREIGN KEY (target_document_id) REFERENCES rag_documents(id) ON DELETE CASCADE,
    CONSTRAINT uq_rag_relationship_pair UNIQUE (source_document_id, target_document_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rag_relationship_queue (
    id INT AUTO_INCREMENT PRIMARY KEY,
    source_document_id INT NOT NULL,
    target_document_id INT NOT NULL,
    priority INT NOT NULL DEFAULT 1000,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    retry_count INT NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    started_at DATETIME(6) NULL,
    completed_at DATETIME(6) NULL,
    last_error TEXT NULL,
    CONSTRAINT fk_rag_relationship_queue_source FOREIGN KEY (source_document_id) REFERENCES rag_documents(id) ON DELETE CASCADE,
    CONSTRAINT fk_rag_relationship_queue_target FOREIGN KEY (target_document_id) REFERENCES rag_documents(id) ON DELETE CASCADE,
    CONSTRAINT uq_rag_relationship_queue_pair_status UNIQUE (source_document_id, target_document_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rag_relationships_source ON rag_relationships (source_document_id, match_status, relationship_type);
CREATE INDEX IF NOT EXISTS idx_rag_relationships_target ON rag_relationships (target_document_id, match_status, relationship_type);
CREATE INDEX IF NOT EXISTS idx_rag_relationships_hashes ON rag_relationships (source_hash, target_hash);
CREATE INDEX IF NOT EXISTS idx_rag_relationship_queue_status ON rag_relationship_queue (status, priority, created_at);
