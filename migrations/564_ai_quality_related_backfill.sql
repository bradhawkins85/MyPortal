-- phase: backfill
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Copy technician votes on ticket Related items into the shared AI feedback
-- tables so they appear with every other AI rating and can teach MyPortal.
-- One output per (ticket, linked item); one vote per voter (any 👎 wins).
-- Safe to re-run. String comparisons are only ever column-to-column within
-- utf8mb4_unicode_ci tables; the ticket side of a link is found numerically.
-- output_hash is sha256('') to match outputs recorded without text.
INSERT INTO ai_outputs
    (feature, subject_type, subject_id, target_type, target_id, output_hash, user_id, company_id,
     model, pipeline_version, latency_ms, outcome)
SELECT 'rag_relationships', 'ticket', v.subject_id, v.target_type, v.target_id,
       'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
       MIN(v.user_id), MAX(t.company_id), MAX(v.evaluated_model), 'output-v1', 0, 'answered'
FROM (
    SELECT f.user_id, f.ticket_id, f.evaluated_model,
           CASE WHEN ds_is_ticket = 1 THEN ds_source_id ELSE dt_source_id END AS subject_id,
           CASE WHEN ds_is_ticket = 1 THEN dt_source_type ELSE ds_source_type END AS target_type,
           CASE WHEN ds_is_ticket = 1 THEN dt_source_id ELSE ds_source_id END AS target_id
    FROM (
        SELECT f.*, ds.source_id AS ds_source_id, ds.source_type AS ds_source_type,
               dt.source_id AS dt_source_id, dt.source_type AS dt_source_type,
               CASE WHEN ds.source_type = 'tickets'
                         AND (CASE WHEN ds.source_type = 'tickets' THEN CAST(ds.source_id AS UNSIGNED) END) = f.ticket_id
                    THEN 1 ELSE 0 END AS ds_is_ticket
        FROM rag_relationship_feedback f
        JOIN rag_relationships r ON r.id = f.relationship_id
        JOIN rag_documents ds ON ds.id = r.source_document_id
        JOIN rag_documents dt ON dt.id = r.target_document_id
    ) f
) v
LEFT JOIN tickets t ON t.id = v.ticket_id
WHERE NOT EXISTS (
    SELECT 1 FROM ai_outputs q
    WHERE q.feature = 'rag_relationships' AND q.subject_type = 'ticket'
      AND q.subject_id = v.subject_id AND q.target_type = v.target_type AND q.target_id = v.target_id
      AND q.output_hash = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
)
GROUP BY v.subject_id, v.target_type, v.target_id;

INSERT INTO ai_output_feedback (response_id, user_id, rating, is_technician, created_at, updated_at)
SELECT q.id, v.user_id, MIN(v.rating), 1, MIN(v.created_at), MAX(v.updated_at)
FROM (
    SELECT f.user_id, f.rating, f.created_at, f.updated_at,
           CASE WHEN ds_is_ticket = 1 THEN ds_source_id ELSE dt_source_id END AS subject_id,
           CASE WHEN ds_is_ticket = 1 THEN dt_source_type ELSE ds_source_type END AS target_type,
           CASE WHEN ds_is_ticket = 1 THEN dt_source_id ELSE ds_source_id END AS target_id
    FROM (
        SELECT f.*, ds.source_id AS ds_source_id, ds.source_type AS ds_source_type,
               dt.source_id AS dt_source_id, dt.source_type AS dt_source_type,
               CASE WHEN ds.source_type = 'tickets'
                         AND (CASE WHEN ds.source_type = 'tickets' THEN CAST(ds.source_id AS UNSIGNED) END) = f.ticket_id
                    THEN 1 ELSE 0 END AS ds_is_ticket
        FROM rag_relationship_feedback f
        JOIN rag_relationships r ON r.id = f.relationship_id
        JOIN rag_documents ds ON ds.id = r.source_document_id
        JOIN rag_documents dt ON dt.id = r.target_document_id
    ) f
) v
JOIN ai_outputs q
  ON q.feature = 'rag_relationships' AND q.subject_type = 'ticket'
 AND q.subject_id = v.subject_id AND q.target_type = v.target_type AND q.target_id = v.target_id
 AND q.output_hash = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
WHERE NOT EXISTS (
    SELECT 1 FROM ai_output_feedback e WHERE e.response_id = q.id AND e.user_id = v.user_id
)
GROUP BY q.id, v.user_id;
