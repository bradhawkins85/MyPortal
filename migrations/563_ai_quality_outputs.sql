-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Let ai_quality_responses record any AI output (what it is about, what it
-- links to, the text itself) so every AI feature can be rated 👍/👎, and
-- snapshot whether each voter was a technician: only technician votes teach.
ALTER TABLE ai_quality_responses ADD COLUMN IF NOT EXISTS subject_type VARCHAR(40) NULL DEFAULT NULL;
ALTER TABLE ai_quality_responses ADD COLUMN IF NOT EXISTS subject_id VARCHAR(64) NULL DEFAULT NULL;
ALTER TABLE ai_quality_responses ADD COLUMN IF NOT EXISTS target_type VARCHAR(40) NULL DEFAULT NULL;
ALTER TABLE ai_quality_responses ADD COLUMN IF NOT EXISTS target_id VARCHAR(64) NULL DEFAULT NULL;
ALTER TABLE ai_quality_responses ADD COLUMN IF NOT EXISTS output_text MEDIUMTEXT NULL;
ALTER TABLE ai_quality_responses ADD COLUMN IF NOT EXISTS output_hash CHAR(64) NULL DEFAULT NULL;
ALTER TABLE ai_quality_responses ADD COLUMN IF NOT EXISTS prompt_version VARCHAR(40) NULL DEFAULT NULL;
ALTER TABLE ai_quality_feedback ADD COLUMN IF NOT EXISTS is_technician TINYINT(1) NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_ai_quality_response_subject ON ai_quality_responses (subject_type, subject_id, feature);
CREATE INDEX IF NOT EXISTS idx_ai_quality_response_target ON ai_quality_responses (target_type, target_id);
