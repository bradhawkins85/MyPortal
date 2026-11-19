-- phase: backfill
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Copy technician votes on ticket Related items into the shared AI feedback
-- tables so they appear with every other AI rating and can teach MyPortal.
-- One output per (ticket, linked item); one vote per voter. Safe to re-run.
-- output_hash is sha256('') to match outputs recorded without text.
INSERT INTO ai_quality_responses
    (user_id, company_id, feature, query_hash, query_redacted, evidence_identifiers, source_types,
     model, pipeline_version, latency_ms, outcome, subject_type, subject_id, target_type, target_id, output_hash)
SELECT MIN(v.user_id), MAX(t.company_id), 'rag_relationships',
       '0000000000000000000000000000000000000000000000000000000000000000', '', '[]', '[]',
       MAX(v.evaluated_model), 'output-v1', 0, 'answered', 'ticket', CAST(v.ticket_id AS CHAR),
       v.target_type, v.target_id,
       'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
FROM (
    SELECT f.user_id, f.ticket_id, f.evaluated_model,
           CASE WHEN ds.source_type = 'tickets' AND ds.source_id = CAST(f.ticket_id AS CHAR)
                THEN dt.source_type ELSE ds.source_type END AS target_type,
           CASE WHEN ds.source_type = 'tickets' AND ds.source_id = CAST(f.ticket_id AS CHAR)
                THEN dt.source_id ELSE ds.source_id END AS target_id
    FROM rag_relationship_feedback f
    JOIN rag_relationships r ON r.id = f.relationship_id
    JOIN rag_documents ds ON ds.id = r.source_document_id
    JOIN rag_documents dt ON dt.id = r.target_document_id
) v
LEFT JOIN tickets t ON t.id = v.ticket_id
WHERE NOT EXISTS (
    SELECT 1 FROM ai_quality_responses q
    WHERE q.feature = 'rag_relationships' AND q.subject_type = 'ticket'
      AND q.subject_id = CAST(v.ticket_id AS CHAR) AND q.target_type = v.target_type AND q.target_id = v.target_id
)
GROUP BY v.ticket_id, v.target_type, v.target_id;

INSERT INTO ai_quality_feedback (response_id, user_id, rating, is_technician, created_at, updated_at)
SELECT q.id, v.user_id, MIN(v.rating), 1, MIN(v.created_at), MAX(v.updated_at)
FROM (
    SELECT f.user_id, f.ticket_id, f.rating, f.created_at, f.updated_at,
           CASE WHEN ds.source_type = 'tickets' AND ds.source_id = CAST(f.ticket_id AS CHAR)
                THEN dt.source_type ELSE ds.source_type END AS target_type,
           CASE WHEN ds.source_type = 'tickets' AND ds.source_id = CAST(f.ticket_id AS CHAR)
                THEN dt.source_id ELSE ds.source_id END AS target_id
    FROM rag_relationship_feedback f
    JOIN rag_relationships r ON r.id = f.relationship_id
    JOIN rag_documents ds ON ds.id = r.source_document_id
    JOIN rag_documents dt ON dt.id = r.target_document_id
) v
JOIN ai_quality_responses q
  ON q.feature = 'rag_relationships' AND q.subject_type = 'ticket'
 AND q.subject_id = CAST(v.ticket_id AS CHAR) AND q.target_type = v.target_type AND q.target_id = v.target_id
WHERE NOT EXISTS (
    SELECT 1 FROM ai_quality_feedback e WHERE e.response_id = q.id AND e.user_id = v.user_id
)
GROUP BY q.id, v.user_id;
