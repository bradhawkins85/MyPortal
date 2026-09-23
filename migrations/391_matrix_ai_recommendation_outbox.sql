-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Durable idempotency/outbox state for Matrix waiting-assistant recommendations.
ALTER TABLE matrix_ai_analysis_queue ADD COLUMN IF NOT EXISTS recommendation_key VARCHAR(64) NULL;
ALTER TABLE matrix_ai_analysis_queue ADD COLUMN IF NOT EXISTS send_status VARCHAR(16) NULL;
ALTER TABLE matrix_ai_analysis_queue ADD COLUMN IF NOT EXISTS matrix_event_id VARCHAR(255) NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_matrix_ai_recommendation_key ON matrix_ai_analysis_queue (recommendation_key);
