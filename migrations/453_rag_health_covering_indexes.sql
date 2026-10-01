-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Let the /admin/rag health statistics be answered from indexes. Without
-- these, chunk counts and token totals scan the clustered rows that hold
-- chunk text and embeddings, which times out on large indexes.
CREATE INDEX idx_rag_chunks_active_doc_tokens
    ON rag_chunks (is_active, document_id, token_count);
CREATE INDEX idx_rag_documents_active_indexed
    ON rag_documents (is_active, indexed_at);
CREATE INDEX idx_rag_index_jobs_created
    ON rag_index_jobs (created_at, id);
