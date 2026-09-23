-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Keep historical reconciliation behind user-triggered and changed-source work.

ALTER TABLE rag_index_outbox ADD COLUMN priority INT NOT NULL DEFAULT 0;

CREATE INDEX idx_rag_index_outbox_priority_ready
    ON rag_index_outbox (status, priority, available_at);
