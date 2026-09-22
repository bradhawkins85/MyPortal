-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Durable, coalescing source-change queue. Workers may be restarted at any point.

ALTER TABLE rag_documents ADD COLUMN source_updated_at DATETIME(6) NULL;
CREATE INDEX idx_rag_documents_source_lag
    ON rag_documents (source_type, source_updated_at, indexed_at);

CREATE TABLE IF NOT EXISTS rag_index_outbox (
    id INT AUTO_INCREMENT PRIMARY KEY,
    source_type VARCHAR(64) NOT NULL,
    source_id VARCHAR(255) NOT NULL,
    action VARCHAR(16) NOT NULL DEFAULT 'upsert',
    source_updated_at DATETIME(6) NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    attempt_count INT NOT NULL DEFAULT 0,
    available_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    claimed_at DATETIME(6) NULL,
    completed_at DATETIME(6) NULL,
    last_error TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_rag_index_outbox_source (source_type, source_id),
    INDEX idx_rag_index_outbox_ready (status, available_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO scheduled_tasks (name, command, cron, active, description)
SELECT 'RAG Incremental Indexing', 'rag_index_incremental', '* * * * *', 1,
       'Retry and coalesce durable RAG source changes.'
WHERE NOT EXISTS (SELECT 1 FROM scheduled_tasks WHERE command = 'rag_index_incremental');

INSERT INTO scheduled_tasks (name, command, cron, active, description)
SELECT 'RAG Full Reconciliation', 'rag_index_reconcile', '17 2 * * *', 1,
       'Paginated reconciliation of all supported RAG records and stale documents.'
WHERE NOT EXISTS (SELECT 1 FROM scheduled_tasks WHERE command = 'rag_index_reconcile');
