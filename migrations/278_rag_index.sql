CREATE TABLE IF NOT EXISTS rag_documents (
    id INT AUTO_INCREMENT PRIMARY KEY,
    source_type VARCHAR(64) NOT NULL,
    source_id VARCHAR(191) NOT NULL,
    company_id INT NULL,
    title VARCHAR(500) NOT NULL,
    url VARCHAR(1000) NULL,
    permission_scope_json LONGTEXT NULL,
    metadata_json LONGTEXT NULL,
    content_hash VARCHAR(64) NOT NULL,
    embedding_model VARCHAR(128) NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    indexed_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT uq_rag_document_source UNIQUE (source_type, source_id, embedding_model)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rag_chunks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    document_id INT NOT NULL,
    chunk_index INT NOT NULL,
    chunk_text LONGTEXT NOT NULL,
    chunk_hash VARCHAR(64) NOT NULL,
    embedding_json LONGTEXT NOT NULL,
    embedding_model VARCHAR(128) NOT NULL,
    token_count INT NOT NULL DEFAULT 0,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    indexed_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_rag_chunks_document FOREIGN KEY (document_id) REFERENCES rag_documents(id) ON DELETE CASCADE,
    CONSTRAINT uq_rag_chunk_document_index UNIQUE (document_id, chunk_index, embedding_model)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rag_index_jobs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    source_type VARCHAR(64) NULL,
    source_id VARCHAR(191) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    message TEXT NULL,
    started_at DATETIME(6) NULL,
    finished_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rag_documents_source ON rag_documents (source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_rag_documents_company_active ON rag_documents (company_id, is_active);
CREATE INDEX IF NOT EXISTS idx_rag_documents_indexed_at ON rag_documents (indexed_at);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_active_model ON rag_chunks (is_active, embedding_model);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_hash ON rag_chunks (chunk_hash);
CREATE INDEX IF NOT EXISTS idx_rag_jobs_status_created ON rag_index_jobs (status, created_at);
CREATE INDEX IF NOT EXISTS idx_rag_jobs_source ON rag_index_jobs (source_type, source_id);
