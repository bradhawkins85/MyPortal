-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 453: Technician 👍/👎 feedback on ticket "Related" items.
-- Each vote is stored against the rag_relationships row it judges, scoped to
-- the ticket it was cast from so a 👎 hides the item from that ticket only.
-- The score, confidence, type and model are snapshotted at vote time because
-- the relationship row is re-evaluated in place; evals/ai_quality reads the
-- snapshot so a label stays tied to the judgement the technician saw.

CREATE TABLE IF NOT EXISTS rag_relationship_feedback (
    id INT AUTO_INCREMENT PRIMARY KEY,
    relationship_id INT NOT NULL,
    ticket_id INT NOT NULL,
    user_id INT NOT NULL,
    rating VARCHAR(4) NOT NULL,
    relationship_type VARCHAR(64) NULL,
    relevance_score DECIMAL(5,4) NULL,
    confidence DECIMAL(5,4) NULL,
    evaluated_model VARCHAR(128) NULL,
    target_source_type VARCHAR(64) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT uq_rag_relationship_feedback_vote UNIQUE (relationship_id, ticket_id, user_id),
    CONSTRAINT fk_rag_relationship_feedback_relationship FOREIGN KEY (relationship_id) REFERENCES rag_relationships(id) ON DELETE CASCADE,
    CONSTRAINT fk_rag_relationship_feedback_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
    CONSTRAINT fk_rag_relationship_feedback_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_rag_relationship_feedback_ticket ON rag_relationship_feedback (ticket_id, rating);
