-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Link learning: remember what each voted ticket was about (its mean RAG
-- embedding) so a technician's 👍/👎 on a Related item can adjust that item
-- for similar tickets, and let admins undo learning by ignoring votes.
ALTER TABLE ai_outputs ADD COLUMN IF NOT EXISTS subject_embedding_json LONGTEXT NULL;
ALTER TABLE ai_outputs ADD COLUMN IF NOT EXISTS subject_embedding_model VARCHAR(128) NULL DEFAULT NULL;
ALTER TABLE ai_output_feedback ADD COLUMN IF NOT EXISTS learning_ignored_at DATETIME(6) NULL DEFAULT NULL;
