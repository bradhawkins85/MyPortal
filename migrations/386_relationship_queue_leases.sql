-- phase: contract
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- DEPLOYMENT: Green/Blue compatible. Existing workers continue to use the queue
-- columns they know while this migration consolidates the pair identity.

UPDATE rag_relationship_queue SET status = UPPER(status);

DELETE FROM rag_relationship_queue
WHERE id NOT IN (
    SELECT keep_id FROM (
        SELECT MAX(id) AS keep_id
        FROM rag_relationship_queue
        GROUP BY source_document_id, target_document_id
    ) AS durable_jobs
);

ALTER TABLE rag_relationship_queue
    DROP INDEX uq_rag_relationship_queue_pair_status,
    ADD UNIQUE KEY uq_rag_relationship_queue_pair (source_document_id, target_document_id);

ALTER TABLE rag_relationship_queue ADD COLUMN claim_token VARCHAR(36) NULL;
ALTER TABLE rag_relationship_queue ADD COLUMN lease_expires_at DATETIME(6) NULL;
ALTER TABLE rag_relationship_queue ADD COLUMN heartbeat_at DATETIME(6) NULL;
ALTER TABLE rag_relationship_queue ADD COLUMN source_hash VARCHAR(64) NOT NULL DEFAULT '';
ALTER TABLE rag_relationship_queue ADD COLUMN target_hash VARCHAR(64) NOT NULL DEFAULT '';

UPDATE rag_relationship_queue
SET status = 'PENDING', started_at = NULL, claim_token = NULL,
    lease_expires_at = NULL, heartbeat_at = NULL
WHERE status = 'PROCESSING';

ALTER TABLE rag_relationship_queue
    MODIFY status ENUM('PENDING','PROCESSING','COMPLETED','SKIPPED','FAILED')
    NOT NULL DEFAULT 'PENDING';

CREATE INDEX idx_rag_relationship_queue_lease
    ON rag_relationship_queue (status, lease_expires_at);
